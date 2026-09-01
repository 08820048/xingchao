"""GitHub 趋势榜单：/trending 指令 + AI 工具共用抓取逻辑。

- 数据源：github.com/trending 页面解析（无官方 API）
- 结果缓存 10 分钟，避免频繁抓取
- 命令：/trending [daily|weekly|monthly] [语言]，超管与群成员均可用
- AI 工具：get_github_trending（LLM 自然语言调用）
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import httpx
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import BASIC

_CACHE: dict[str, Any] = {"key": "", "items": [], "ts": 0.0}
_CACHE_TTL = 600

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _parse_trending(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in re.findall(r'<article class="Box-row">(.*?)</article>', html, re.S):
        repo = re.search(r'<h2[^>]*>.*?href="(/[^"]+?)"', block, re.S)
        if not repo:
            continue
        name = repo.group(1).strip("/")
        desc_m = re.search(r'<p class="[^"]*col-9[^"]*">\s*(.*?)\s*</p>', block, re.S)
        desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
        star_m = re.search(r'href="/[^"]+/stargazers"[^>]*>.*?</svg>\s*([\d,\s]+)', block, re.S)
        stars = star_m.group(1).strip().replace(",", "") if star_m else "?"
        star_m2 = re.search(r'([\d,]+)\s*stars today', block) or \
            re.search(r'([\d,]+)\s*stars this (week|month)', block)
        gained = star_m2.group(1).replace(",", "") if star_m2 else "?"
        items.append({"repo": name, "desc": desc[:80], "stars": stars, "gained": gained})
        if len(items) >= 10:
            break
    return items


async def fetch_trending(since: str = "daily", language: str = "") -> list[dict[str, Any]]:
    """抓取 GitHub Trending；10 分钟缓存。失败抛异常由调用方处理。"""
    since = since if since in ("daily", "weekly", "monthly") else "daily"
    key = f"{since}:{language}"
    if _CACHE["key"] == key and time.time() - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["items"]

    url = f"https://github.com/trending/{language.strip()}?since={since}" \
        if language.strip() else f"https://github.com/trending?since={since}"
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}, follow_redirects=True) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        items = _parse_trending(resp.text)
    if not items:
        raise ValueError("解析趋势页面失败（页面结构可能已变化）")
    _CACHE.update(key=key, items=items, ts=time.time())
    return items


def format_trending(items: list[dict[str, Any]], since: str, language: str) -> str:
    since_label = {"daily": "今日", "weekly": "本周", "monthly": "本月"}.get(since, since)
    head = f"📈 GitHub {since_label}趋势" + (f"（{language}）" if language.strip() else "") + "\n"
    lines = [
        f"{i}. {it['repo']}（⭐ {it['stars']}" + (f"，{since_label}+{it['gained']}" if it["gained"] != "?" else "") + ")"
        + (f"\n    {it['desc']}" if it["desc"] else "")
        for i, it in enumerate(items, 1)
    ]
    period = {
        "daily": datetime.now().strftime("%Y-%m-%d"),
        "weekly": "周榜",
        "monthly": "月榜",
    }.get(since, "")
    return head + "\n".join(lines) + (f"\n\n📅 {period}" if period else "")


trending_cmd = on_command("trending", aliases={"趋势", "榜单"}, rule=BASIC, priority=5, block=True)


@trending_cmd.handle()
async def handle_trending(matcher: Matcher, args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split()
    since = parts[0] if parts and parts[0] in ("daily", "weekly", "monthly") else "daily"
    language = parts[1] if len(parts) > 1 and since != parts[0] else \
        (parts[0] if parts and parts[0] not in ("daily", "weekly", "monthly") else "")
    try:
        items = await fetch_trending(since, language)
        await matcher.send(format_trending(items, since, language))
    except MatcherException:
        raise
    except Exception as e:
        logger.exception("获取 GitHub 趋势失败")
        await matcher.send(f"获取 GitHub 趋势失败：{e}")
