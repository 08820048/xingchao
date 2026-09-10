"""链接自动解读：白名单群有人发链接时，抓取网页信息并调用 AI 用一句话概括，
引用原消息回复到群里。

- 仅白名单群；跳过 @机器人 的消息（交给 AI 问答）、指令消息、GitHub 链接（githubcard 已处理）
- 每群冷却 link_preview_cooldown 秒；每群每日上限 link_preview_daily_limit 次
- 开关 link_preview_enabled（面板模块开关 / /plugin link on|off）
- HTTP 抓取做 SSRF 防护（拒绝内网/回环地址），失败静默（仅 debug 日志），不刷屏
- AI 输出经 md_to_qq 降级；AI 未配置/未开启时功能自动跳过
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.markdown import md_to_qq
from src.permission import GROUP_WHITELIST
from src.store import get_store

DEFAULTS: dict[str, object] = {
    "link_preview_enabled": True,
    "link_preview_cooldown": 30,       # 每群冷却秒数
    "link_preview_daily_limit": 50,    # 每群每日解读上限
}

# 跳过已有专门处理的站点（githubcard 会发卡片，避免重复）
_SKIP_HOSTS = {"github.com", "www.github.com", "gist.github.com"}

_URL_RE = re.compile(r'https?://[^\s<>"\')\]}，。；！？、]+', re.IGNORECASE)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_VISIBLE_BLOCK_RE = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")

_USER_AGENT = "Mozilla/5.0 (compatible; XingchaoBot/1.0; +https://xingchao.dev)"
_MAX_BYTES = 512 * 1024  # 最多读取 512KB，避免大文件拖垮

_last_reply: dict[int, float] = {}  # group_id -> monotonic 时间戳


# ---------------------------------------------------------------- 配置

async def _kv(key: str):
    raw = await get_store().get_kv(key)
    if raw is None:
        raw = DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


async def link_preview_enabled() -> bool:
    return await _kv("link_preview_enabled") is True


async def _bump_usage(group_id: int) -> None:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"link_preview_usage_{day}"
    raw = await store.get_kv(key)
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    usage[str(group_id)] = usage.get(str(group_id), 0) + 1
    await store.set_kv(key, json.dumps(usage, ensure_ascii=False))


async def _daily_count(group_id: int) -> int:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"link_preview_usage_{day}")
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return 0
    return int(usage.get(str(group_id), 0))


# ---------------------------------------------------------------- 抓取与解析

def _host_is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


async def _host_allowed(host: str) -> bool:
    if not host:
        return False
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return False
    if not infos:
        return False
    return all(not _host_is_private(info[4][0]) for info in infos)


def _parse_metas(page: str) -> dict[str, str]:
    metas: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(page):
        attrs: dict[str, str] = {}
        for m in _ATTR_RE.finditer(tag):
            value = m.group(2) or m.group(3) or m.group(4) or ""
            attrs[m.group(1).lower()] = html.unescape(value)
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        if name and name not in metas:
            metas[name] = attrs.get("content", "").strip()
    return metas


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _extract_page_info(page: str) -> dict[str, str]:
    metas = _parse_metas(page)
    title_match = _TITLE_RE.search(page)
    title = (
        metas.get("og:title") or metas.get("twitter:title")
        or (_clean(title_match.group(1)) if title_match else "")
    )
    description = (
        metas.get("og:description") or metas.get("description")
        or metas.get("twitter:description") or ""
    )
    visible = _VISIBLE_BLOCK_RE.sub(" ", page)
    visible = _TAG_RE.sub(" ", visible)
    visible = _clean(visible)
    return {
        "title": title[:200],
        "description": _clean(description)[:400],
        "text": visible[:800],
    }


async def _fetch_page_info(url: str) -> dict[str, str] | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if not await _host_allowed(parsed.hostname):
        logger.debug(f"链接解读：拒绝内网/非法地址 {url}")
        return None
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=headers) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                ctype = resp.headers.get("content-type", "").lower()
                if "html" not in ctype and "xhtml" not in ctype:
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_BYTES:
                        break
                page = b"".join(chunks).decode(resp.encoding or "utf-8", errors="ignore")
    except Exception:
        logger.debug(f"链接解读：抓取失败 {url}", exc_info=True)
        return None
    info = _extract_page_info(page)
    if not info["title"] and not info["description"] and not info["text"]:
        return None
    return info


def _build_prompt(url: str, info: dict[str, str]) -> str:
    return (
        "请阅读下面的网页信息，用一句话（不超过 50 字）概括这个链接讲了什么。\n"
        "只输出这一句话，不要引号、不要前缀、不要 Markdown、不要复述本要求。\n\n"
        f"链接：{url}\n"
        f"标题：{info['title'] or '（无）'}\n"
        f"描述：{info['description'] or '（无）'}\n"
        f"正文摘录：{info['text'] or '（无）'}"
    )


# ---------------------------------------------------------------- 处理

link_preview_matcher = on_message(rule=GROUP_WHITELIST, priority=6, block=False)


@link_preview_matcher.handle()
async def handle_link_preview(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id) or event.to_me:
        return
    if not await link_preview_enabled():
        return

    from src.plugins import ai as ai_plugin

    if not await ai_plugin.is_ai_enabled():
        return

    text = event.message.extract_plain_text()
    urls = [u.rstrip(".,;:!?") for u in _URL_RE.findall(text)]
    urls = [
        u for u in urls
        if (urlparse(u).hostname or "").lower() not in _SKIP_HOSTS
    ]
    if not urls:
        return

    # 每群冷却
    now = time.monotonic()
    cooldown = int(await _kv("link_preview_cooldown"))
    if now - _last_reply.get(event.group_id, 0.0) < cooldown:
        return
    # 每群每日上限
    if await _daily_count(event.group_id) >= int(await _kv("link_preview_daily_limit")):
        return

    url = urls[0]
    info = await _fetch_page_info(url)
    if info is None:
        return

    summary = await ai_plugin.generate_text(
        _build_prompt(url, info),
        system="你是网页摘要助手，回答简洁准确，只输出一句话。",
        temperature=0.3,
    )
    if not summary:
        return

    _last_reply[event.group_id] = now
    await _bump_usage(event.group_id)
    summary = md_to_qq(summary).strip().replace("\n", " ")
    message = MessageSegment.reply(event.message_id) + f"🔗 链接解读：{summary}"
    try:
        await matcher.send(message)
    except MatcherException:
        raise
    except Exception:
        logger.exception("链接解读回复发送失败")
