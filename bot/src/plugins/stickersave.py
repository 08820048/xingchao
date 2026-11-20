"""存表情：群友回复一张表情包说「存表情」就存进机器人的表情包库；机器人也会自主收下群里流行的表情。

- 指令存：回复一张图（或图和话一起发）说「存表情 / 存一下 / 偷表情 / 收藏」（带不带 / 都行）。
  所有群友可用，每人每天最多 DAILY_PER_USER 张；存之前过一遍视觉审核和二维码检查，不合适的不存
- 自主收集：同一张表情包 AUTO_WINDOW 内被 AUTO_MIN_SENDERS 个不同的人发过，就悄悄收下（不在群里说话）；
  每天最多 AUTO_DAILY 张，库用到 AUTO_LIBRARY_RATIO 以上就不再自主收
- /表情包：看库里有多少、最近存了什么；/表情包 看 <编号> 发出来；/表情包 删除 <编号>（仅超管）
- 开关：/模块 表情 开|关（sticker_collect_enabled，指令存与自主收集都关）；
  kv sticker_auto_collect=false 只关自主收集
怎么用这些表情见 stickers.py（主动发言与 AI 工具 send_sticker 会按聊天内容挑）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import Rule

from src.background import spawn
from src.permission import BASIC, GROUP_WHITELIST, SUPERUSER
from src.plugins import stickers
from src.store import get_store

DAILY_PER_USER = 10
AUTO_MIN_SENDERS = 2
AUTO_WINDOW = 3 * 86400
AUTO_DAILY = 10
AUTO_LIBRARY_RATIO = 0.8
_SIGHTINGS_MAX = 3000

TRIGGERS = frozenset({
    "存表情", "存表情包", "存一下", "存下", "存起来", "偷表情", "偷表情包", "偷了", "偷一下",
    "收藏", "收藏一下", "存图",
})
_STRIP_RE = re.compile(r"^(?:/|星潮)\s*|[\s!！~～。.，,]+$")

RESULT_TEXT = {
    "duplicate": "这张早就存过啦（{label}）",
    "too_big": "这张太大了（超过 3MB），存不下",
    "not_image": "这个不是图片，存不了",
    "full": "表情包库满了，让管理员用 /表情包 清理一下再存",
    "qrcode": "图里有二维码，这种不存",
    "no_ai": "我现在看不了图（AI 没开），先存不了",
    "unsafe": "这张不太合适，就不存了",
    "failed": "没看懂这张图，存失败了，等会儿再试试",
}

# 自主收集的观察记录：图片 key → {"users": {QQ}, "first": 时间, "seg": 最近一次的段, "group_id": 群}
_sightings: dict[str, dict[str, Any]] = {}
_done: set[str] = set()  # 已处理过（存了或被拒）的 key，不再重复下载


# ---------------------------------------------------------------- 开关

async def _flag(key: str, default: bool = True) -> bool:
    raw = await get_store().get_kv(key)
    return default if raw is None else raw != "false"


async def collect_enabled() -> bool:
    return await _flag("sticker_collect_enabled")


async def auto_enabled() -> bool:
    return await collect_enabled() and await _flag("sticker_auto_collect")


# ---------------------------------------------------------------- 取图

def normalize_trigger(text: str) -> str:
    return _STRIP_RE.sub("", (text or "").strip()).strip()


def _image_segments(message: Message) -> list[MessageSegment]:
    """消息里能存的图：普通图片 / 动画表情，以及带 url 的商城表情（mface）。"""
    out = []
    for seg in message:
        if seg.type == "image":
            out.append(seg)
        elif seg.type == "mface" and str(seg.data.get("url") or "").startswith("http"):
            out.append(MessageSegment("image", {"url": seg.data["url"], "file": seg.data.get("key") or "",
                                                "sub_type": 1, "summary": seg.data.get("summary") or ""}))
    return out


def _to_message(raw: Any) -> Message:
    if isinstance(raw, str):
        return Message(raw)
    segs = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("type"):
            segs.append(MessageSegment(str(item["type"]), dict(item.get("data") or {})))
    return Message(segs)


async def _target_image(bot: Bot, event: GroupMessageEvent) -> MessageSegment | None:
    """优先取被回复的那条消息里的图，其次是这条消息自己带的图。"""
    if event.reply is not None:
        found = _image_segments(event.reply.message)
        if not found:
            try:
                data = await bot.call_api("get_msg", message_id=event.reply.message_id)
                found = _image_segments(_to_message((data or {}).get("message")))
            except Exception:
                logger.debug("存表情：获取被回复消息失败", exc_info=True)
        if found:
            return found[0]
    found = _image_segments(event.message)
    return found[0] if found else None


async def _image_bytes(seg: MessageSegment) -> bytes | None:
    from src.plugins.visionguard import _image_bytes

    return await _image_bytes(seg)


def _today_start() -> str:
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds")


# ---------------------------------------------------------------- 指令存

async def _is_save_request(event: GroupMessageEvent) -> bool:
    if normalize_trigger(event.message.extract_plain_text()) not in TRIGGERS:
        return False
    if event.reply is None and not _image_segments(event.message):
        return False
    return await collect_enabled()


save_matcher = on_message(rule=GROUP_WHITELIST & Rule(_is_save_request), priority=5, block=True)


@save_matcher.handle()
async def handle_save(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    reply_to = MessageSegment.reply(event.message_id)
    seg = await _target_image(bot, event)
    if seg is None:
        await matcher.finish(reply_to + "要存哪张？回复那张表情包再说「存表情」")
    store = get_store()
    if await store.count_stickers_saved(since=_today_start(), saved_by=event.user_id) >= DAILY_PER_USER:
        await matcher.finish(reply_to + f"你今天已经存了 {DAILY_PER_USER} 张了，明天再来～")
    data = await _image_bytes(seg)
    if not data:
        await matcher.finish(reply_to + "这张图我拿不到了（可能太久了），重新发一下再存吧")
    code, row = await stickers.save_image(
        data, group_id=event.group_id, saved_by=event.user_id, source="cmd")
    _done.add(_sighting_key(seg))
    if code == "ok" and row:
        logger.info(f"存表情：群 {event.group_id} 用户 {event.user_id} 存了 {stickers.describe(row)}")
        await matcher.finish(reply_to + f"存好啦 {stickers.describe(row)}")
    label = stickers.describe(row) if row else ""
    await matcher.finish(reply_to + RESULT_TEXT.get(code, RESULT_TEXT["failed"]).format(label=label))


# ---------------------------------------------------------------- 自主收集

def _sighting_key(seg: MessageSegment) -> str:
    """QQ 图片的 file 字段是内容哈希命名，同一张表情不同人发也相同；没有就用去掉签名参数的 url。"""
    file = str(seg.data.get("file") or "").strip()
    if file:
        return file
    return str(seg.data.get("url") or "").split("?")[0]


def note_sighting(key: str, user_id: int, group_id: int, seg: MessageSegment,
                  now: float | None = None) -> bool:
    """记一次「有人发了这张表情」；达到自主收集条件（且之前没处理过）时返回 True。"""
    now = time.time() if now is None else now
    if not key or key in _done:
        return False
    rec = _sightings.get(key)
    if rec is None or now - rec["first"] > AUTO_WINDOW:
        if len(_sightings) >= _SIGHTINGS_MAX:
            for k in sorted(_sightings, key=lambda k: _sightings[k]["first"])[: _SIGHTINGS_MAX // 10]:
                _sightings.pop(k, None)
        rec = _sightings[key] = {"users": set(), "first": now, "seg": seg, "group_id": group_id}
    rec["users"].add(user_id)
    rec["seg"], rec["group_id"] = seg, group_id  # 用最新的段（url 签名会过期）
    if len(rec["users"]) >= AUTO_MIN_SENDERS:
        _done.add(key)
        _sightings.pop(key, None)
        return True
    return False


async def _auto_save(seg: MessageSegment, group_id: int) -> None:
    store = get_store()
    if await store.count_stickers_saved(since=_today_start(), source="auto") >= AUTO_DAILY:
        return
    if len(await stickers._local_stickers()) >= stickers.MAX_LIBRARY * AUTO_LIBRARY_RATIO:
        return
    data = await _image_bytes(seg)
    if not data:
        return
    code, row = await stickers.save_image(data, group_id=group_id, saved_by=0, source="auto")
    if code == "ok" and row:
        logger.info(f"自主收集表情包：群 {group_id} {stickers.describe(row)}")
    else:
        logger.debug(f"自主收集表情包未存（{code}）：群 {group_id}")


async def _has_sticker(event: GroupMessageEvent) -> bool:
    return any(_is_sticker(seg) for seg in _image_segments(event.message))


def _is_sticker(seg: MessageSegment) -> bool:
    return str(seg.data.get("sub_type", "0")) == "1" or "表情" in str(seg.data.get("summary", ""))


# priority 7：在图片审核（4，违规图会撤回并拦截）之后、AI / 主动发言之前，不被它们拦掉
watch_matcher = on_message(rule=GROUP_WHITELIST & Rule(_has_sticker), priority=7, block=False)


@watch_matcher.handle()
async def handle_watch(bot: Bot, event: GroupMessageEvent) -> None:
    if event.user_id == int(bot.self_id) or not await auto_enabled():
        return
    for seg in _image_segments(event.message):
        if _is_sticker(seg) and note_sighting(_sighting_key(seg), event.user_id, event.group_id, seg):
            spawn(_auto_save(seg, event.group_id), "自主收集表情包")


# ---------------------------------------------------------------- /表情包

library_cmd = on_command("表情包", aliases={"表情库"}, rule=BASIC, priority=1, block=True)


@library_cmd.handle()
async def handle_library(bot: Bot, event: MessageEvent, matcher: Matcher,
                         args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().split()
    action = parts[0] if parts else "列表"
    num = int(parts[1].lstrip("#")) if len(parts) > 1 and parts[1].lstrip("#").isdigit() else None

    if action in ("看", "发", "查看") and num is not None:
        msg = await stickers.sticker_message(f"L{num}")
        await matcher.finish(msg if msg else f"没有 #{num} 这张")
    if action in ("删除", "删", "移除") and num is not None:
        if not await SUPERUSER(bot, event, {}):
            await matcher.finish("删除表情包需要超管权限")
        row = await stickers.delete(num)
        await matcher.finish(f"已删除 {stickers.describe(row)}" if row else f"没有 #{num} 这张")

    rows = await stickers._local_stickers()
    if not rows:
        await matcher.finish("表情包库还是空的。回复一张表情包说「存表情」就能存进来～")
    lines = [f"表情包库共 {len(rows)} 张，最近存的："]
    lines += [stickers.describe(r) for r in rows[:10]]
    lines.append("/表情包 看 <编号> 发出来；超管可 /表情包 删除 <编号>")
    await matcher.finish("\n".join(lines))
