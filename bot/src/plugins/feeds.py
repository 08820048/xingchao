"""内容订阅推送：在合适的时候抓取指定内容源，推送到群。

- 订阅 = 目标群 + 内容源 + 时间 + 重复规则（daily/weekdays/weekend/weekly）+ 参数
- 内容源是可插拔的小函数（SOURCES 注册表）：zaobao（MuseAI 早报）、github（趋势榜单）
- "合适的时候"：到点后若内容未就绪（如早报还没发布）不会发旧内容，每 10 分钟重试，
  超过窗口（默认 120 分钟）仍失败则放弃并私聊超管一次
- 去重：kv feed_last_{订阅id} 记录最近发送日期，重启不会重发
- 指令：/feed list、/feed now <id>（超管）；增删改在 Web 管理面板「内容推送」页
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
from nonebot import get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger
from nonebot.params import CommandArg

from src.permission import SUPERUSER
from src.plugins.scheduler import _matches_today
from src.store import get_store

ZAOBAO_BASE = "https://zaobao-aio.pages.dev"
RETRY_INTERVAL = 600  # 内容未就绪 / 抓取失败后的重试间隔（秒）
WINDOW_MINUTES = 120  # 从设定时间起最多重试多久
REPEATS = ("daily", "weekdays", "weekend", "weekly")
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_worker_started = False
_next_try: dict[int, float] = {}  # sub_id -> 下次允许尝试的时间戳
_failed: dict[int, tuple[str, str]] = {}  # sub_id -> (日期, 最近一次失败原因)
_notified: set[tuple[int, str]] = set()
_cache: dict[tuple, "FeedContent"] = {}


@dataclass
class FeedContent:
    """head 作为普通消息发送；items 非空时再以合并转发发送（失败回退为分段文本）。"""

    head: str
    items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 早报解析

def parse_zaobao(page: str) -> dict[str, Any] | None:
    """解析早报某期页面；非"某月某日 AI 早报"页面（含站点对未发布日期的首页回退）返回 None。"""
    h1 = re.search(r"<h1[^>]*>\s*(\d{1,2})月(\d{1,2})日\s*AI\s*早报\s*</h1>", page)
    if not h1:
        return None
    items: list[dict[str, str]] = []
    for block in re.findall(r'<article class="story"[^>]*>(.*?)</article>', page, re.S):
        title_m = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        p_m = re.search(r"<p>(.*?)</p>", block, re.S)
        if not title_m or not p_m:
            continue
        link_m = re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>', p_m.group(1), re.S)
        body = re.sub(r"<a [^>]*>.*?</a>", "", p_m.group(1), flags=re.S)
        clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()  # noqa: E731
        items.append({
            "title": clean(title_m.group(1)),
            "body": clean(body),
            "url": html.unescape(link_m.group(1)) if link_m else "",
            "source": clean(link_m.group(2)) if link_m else "",
        })
    if not items:
        return None
    return {"month": int(h1.group(1)), "day": int(h1.group(2)), "items": items}


def format_zaobao(parsed: dict[str, Any], page_url: str, bodies: list[str] | None = None) -> FeedContent:
    items = parsed["items"]
    head_lines = [f"📰 {parsed['month']}月{parsed['day']}日 AI 早报（共 {len(items)} 条）"]
    head_lines += [f"{i}. {it['title']}" for i, it in enumerate(items, 1)]
    head_lines.append(f"\n👇 详情见下方合并转发\n🔗 {page_url}")
    nodes = []
    for i, it in enumerate(items):
        body = bodies[i] if bodies and i < len(bodies) and bodies[i] else it["body"]
        tail = f"\n来源：{it['source']} {it['url']}".rstrip() if it["url"] else ""
        nodes.append(f"{i + 1}. {it['title']}\n{body}{tail}")
    return FeedContent(head="\n".join(head_lines), items=nodes)


async def _http_get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}, follow_redirects=True) as c:
        return await c.get(url)


async def fetch_zaobao(params: dict, ai: bool, now: datetime) -> FeedContent | None:
    """今天这期未发布返回 None（不推旧内容）；网络错误抛异常。"""
    url = f"{ZAOBAO_BASE}/{now.strftime('%Y-%m-%d')}.html"
    resp = await _http_get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    parsed = parse_zaobao(resp.text)
    # 站点对未发布的日期会回退到首页(HTTP 200)，必须校验页面标题的日期
    if not parsed or (parsed["month"], parsed["day"]) != (now.month, now.day):
        return None
    bodies = await ai_lines(
        "把下列每条科技资讯压缩成不超过 60 字的中文要点，保留关键数字与主体，不要添加原文没有的信息。",
        [f"{it['title']}。{it['body']}" for it in parsed["items"]],
    ) if ai else None
    return format_zaobao(parsed, url, bodies)


async def fetch_github(params: dict, ai: bool, now: datetime) -> FeedContent:
    from src.plugins.github import fetch_trending, format_trending

    since = params.get("since", "daily")
    language = str(params.get("language", ""))
    items = await fetch_trending(since, language)  # 解析为空会抛 ValueError，由调用方报警
    if ai:
        zh = await ai_lines(
            "把下列 GitHub 仓库简介翻译成简洁自然的中文（不超过 40 字），专有名词保持原样。",
            [it["desc"] or "（无简介）" for it in items],
        )
        if zh:
            items = [{**it, "desc": zh[i] if zh[i] != "（无简介）" else it["desc"]} for i, it in enumerate(items)]
    return FeedContent(head=format_trending(items, since, language))


SOURCES: dict[str, tuple[str, Callable[[dict, bool, datetime], Awaitable[FeedContent | None]]]] = {
    "zaobao": ("MuseAI 早报", fetch_zaobao),
    "github": ("GitHub 趋势榜单", fetch_github),
}


# ---------------------------------------------------------------- AI 辅助

def parse_numbered_lines(text: str, expected: int) -> list[str] | None:
    """解析 "1. xxx" 形式的逐条输出；条数对不上视为失败。"""
    out: dict[int, str] = {}
    for m in re.finditer(r"^\s*(\d+)[.、)）]\s*(.+?)\s*$", text, re.M):
        out[int(m.group(1))] = m.group(2)
    if len(out) != expected or set(out) != set(range(1, expected + 1)):
        return None
    return [out[i] for i in range(1, expected + 1)]


async def ai_lines(instruction: str, texts: list[str]) -> list[str] | None:
    """让 AI 逐条改写；任何失败返回 None，调用方回退到原文。"""
    try:
        from src.plugins.ai import generate_text

        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
        out = await generate_text(
            f"{instruction}\n严格按「编号. 内容」逐行输出，共 {len(texts)} 行，不要输出其他文字。\n\n{numbered}",
            max_tokens=3000,
        )
        return parse_numbered_lines(out, len(texts)) if out else None
    except Exception:
        logger.exception("内容推送 AI 改写失败，回退原文")
        return None


# ---------------------------------------------------------------- 调度判断

def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def slot_state(sub: dict, now: datetime) -> str:
    """skip=今天不推 / before=未到点 / due=在窗口内 / expired=窗口已过。"""
    if not _matches_today(sub, now):
        return "skip"
    cur, start = now.hour * 60 + now.minute, _minutes(sub["time"])
    if cur < start:
        return "before"
    return "due" if cur <= start + WINDOW_MINUTES else "expired"


def parse_params(sub: dict) -> dict:
    try:
        v = json.loads(sub.get("params") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


async def get_content(sub: dict, now: datetime) -> FeedContent | None:
    _, fetch = SOURCES[sub["source"]]
    day = now.strftime("%Y-%m-%d")
    key = (sub["source"], sub.get("params") or "{}", sub["ai_summary"], day)
    if key in _cache:
        return _cache[key]
    content = await fetch(parse_params(sub), sub["ai_summary"], now)
    if content is not None:
        for k in [k for k in _cache if k[3] != day]:
            _cache.pop(k, None)
        _cache[key] = content
    return content


# ---------------------------------------------------------------- 发送

def _chunk(items: list[str], limit: int = 1500) -> list[str]:
    chunks, cur = [], ""
    for it in items:
        if cur and len(cur) + len(it) + 2 > limit:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{it}" if cur else it
    return chunks + ([cur] if cur else [])


async def send_content(bot, group_id: int, content: FeedContent) -> None:
    await bot.call_api("send_group_msg", group_id=group_id, message=content.head)
    if not content.items:
        return
    nodes = [
        {"type": "node", "data": {"name": "星潮", "uin": str(bot.self_id), "content": text}}
        for text in content.items
    ]
    try:
        await bot.call_api("send_group_forward_msg", group_id=group_id, messages=nodes)
    except Exception:
        logger.warning(f"合并转发失败，回退为分段文本：群 {group_id}")
        for chunk in _chunk(content.items):
            await bot.call_api("send_group_msg", group_id=group_id, message=chunk)


async def run_sub(bot, sub: dict, now: datetime | None = None) -> tuple[bool, str]:
    """执行一次推送（不写去重标记）。返回 (是否已发送, 说明)。"""
    now = now or datetime.now()
    try:
        content = await get_content(sub, now)
    except Exception as e:
        logger.exception(f"内容推送 #{sub['id']} 抓取失败")
        return False, f"抓取失败：{e}"
    if content is None:
        return False, "今天的内容尚未发布"
    try:
        await send_content(bot, sub["group_id"], content)
    except Exception as e:
        logger.exception(f"内容推送 #{sub['id']} 发送失败：群 {sub['group_id']}")
        return False, f"发送失败：{e}"
    logger.info(f"内容推送 #{sub['id']}（{sub['source']}）已发送：群 {sub['group_id']}")
    return True, "已发送"


async def _notify_superusers(bot, text: str) -> None:
    for uid in get_driver().config.superusers:
        try:
            await bot.call_api("send_private_msg", user_id=int(uid), message=text)
        except Exception:
            logger.warning(f"通知超管 {uid} 失败")


async def _tick(bot) -> None:
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    store = get_store()
    for sub in await store.list_feed_subs(enabled_only=True):
        if sub["source"] not in SOURCES:
            continue
        sid = sub["id"]
        state = slot_state(sub, now)
        if state in ("skip", "before"):
            continue
        if await store.get_kv(f"feed_last_{sid}") == day:
            continue
        if state == "expired":
            if sid in _failed and _failed[sid][0] == day and (sid, day) not in _notified:
                _notified.add((sid, day))
                label = SOURCES[sub["source"]][0]
                await _notify_superusers(
                    bot, f"⚠️ 内容推送 #{sid}「{label}」今日未能推送到群 {sub['group_id']}：{_failed[sid][1]}"
                )
            continue
        if time.time() < _next_try.get(sid, 0):
            continue
        ok, msg = await run_sub(bot, sub, now)
        if ok:
            await store.set_kv(f"feed_last_{sid}", day)
            _failed.pop(sid, None)
        else:
            _failed[sid] = (day, msg)
            _next_try[sid] = time.time() + RETRY_INTERVAL
            logger.info(f"内容推送 #{sid} 暂未推送（{msg}），{RETRY_INTERVAL // 60} 分钟后重试")


async def _worker() -> None:
    from nonebot import get_bot

    logger.info("内容推送调度器已启动（每 30 秒扫描）")
    while True:
        try:
            await _tick(get_bot())
        except Exception:
            logger.exception("内容推送扫描异常")
        await asyncio.sleep(30)


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())


# ---------------------------------------------------------------- 指令

WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
REPEAT_LABEL = {"daily": "每天", "weekdays": "工作日", "weekend": "周末", "weekly": "每周"}

feed_cmd = on_command("feed", rule=SUPERUSER, priority=1, block=True)


@feed_cmd.handle()
async def handle_feed(matcher, bot: Bot, args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split()
    subs = await get_store().list_feed_subs()
    if not parts or parts[0] in ("list", "列表"):
        if not subs:
            await matcher.send("当前没有内容推送订阅（面板「内容推送」页可添加）。")
            return
        lines = ["内容推送订阅："]
        for s in subs:
            rep = REPEAT_LABEL.get(s["repeat"], s["repeat"])
            if s["repeat"] == "weekly" and s["weekday"] is not None:
                rep += WEEKDAY_NAME[s["weekday"]]
            lines.append(
                f"  #{s['id']} [{s['time']}] {rep} {SOURCES.get(s['source'], (s['source'],))[0]}"
                f" → 群 {s['group_id']} {'✅' if s['enabled'] else '⏸'}"
            )
        await matcher.send("\n".join(lines))
        return
    if parts[0] in ("now", "立即") and len(parts) > 1 and parts[1].isdigit():
        sub = next((s for s in subs if s["id"] == int(parts[1])), None)
        if sub is None or sub["source"] not in SOURCES:
            await matcher.send("订阅不存在。")
            return
        ok, msg = await run_sub(bot, sub)
        await matcher.send(f"#{sub['id']}：{msg}")
        return
    await matcher.send("用法：/feed list 查看订阅；/feed now <id> 立即推送一次（不影响当天定时推送）。")
