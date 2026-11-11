"""群精华消息：设为 / 取消 / 查看精华（机器人需要是群管理员），并支持机器人向管理员推荐精华。

- 指令（仅超管）：/精华 [message_id] [群号]（回复目标消息即可）、/取消精华、/精华列表
  私聊使用时要带群号：/精华 <message_id> <群号>
- AI 工具：set_essence_message / delete_essence_message（超管）、list_essence_messages（所有人）
- 推荐：主动发言的决策里有 nominate 动作，机器人觉得某条回答特别有价值时，私聊管理员
  "建议设为精华"并附指令，管理员一句话确认；每群每天最多推荐 3 次，同一条只推荐一次
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import SUPERUSER

NOMINATE_DAILY_LIMIT = 3
_nominated: dict[int, float] = {}                # message_id → ts
_nominate_daily: dict[tuple[int, str], int] = {}


def _err(e: Exception) -> str:
    info = getattr(e, "info", None)
    text = str(info or e)
    if any(k in text for k in ("权限", "permission", "admin", "管理员")):
        return "机器人需要是该群管理员才能操作精华"
    return text[:80]


async def set_essence(bot: Bot, message_id: int) -> tuple[bool, str]:
    try:
        await bot.call_api("set_essence_msg", message_id=message_id)
        return True, "已设为精华 ⭐"
    except Exception as e:
        return False, _err(e)


async def delete_essence(bot: Bot, message_id: int) -> tuple[bool, str]:
    try:
        await bot.call_api("delete_essence_msg", message_id=message_id)
        return True, "已取消精华"
    except Exception as e:
        return False, _err(e)


def format_essence_list(rows: Any, limit: int = 10) -> str:
    if not isinstance(rows, list) or not rows:
        return "本群还没有精华消息。"
    lines = [f"⭐ 精华消息（共 {len(rows)} 条，最近 {min(limit, len(rows))} 条）"]
    for r in reversed(rows[-limit:]):
        who = r.get("sender_nick") or r.get("sender_id") or "?"
        when = r.get("sender_time") or r.get("operator_time")
        day = datetime.fromtimestamp(int(when)).strftime("%m-%d") if str(when).isdigit() else ""
        content = r.get("content")
        if isinstance(content, list):  # 消息段数组 → 取文字
            content = "".join(str(s.get("data", {}).get("text", "")) for s in content if isinstance(s, dict))
        lines.append(f"· {day} {who}：{str(content or '（非文字消息）')[:40]}".strip())
    return "\n".join(lines)


async def list_essence(bot: Bot, group_id: int) -> str:
    try:
        return format_essence_list(await bot.call_api("get_essence_msg_list", group_id=group_id))
    except Exception as e:
        return f"获取精华列表失败：{_err(e)}"


def _target(event: MessageEvent, args: Message) -> tuple[int | None, int | None]:
    """从指令参数/回复里取 (message_id, group_id)。"""
    parts = args.extract_plain_text().split()
    nums = [int(p) for p in parts if p.lstrip("-").isdigit()]
    mid = None
    gid = event.group_id if isinstance(event, GroupMessageEvent) else None
    if event.reply is not None:
        mid = event.reply.message_id
        if nums and gid is None:
            gid = nums[0]
    elif nums:
        mid = nums[0]
        if len(nums) > 1 and gid is None:
            gid = nums[1]
    return mid, gid


essence_cmd = on_command("精华", rule=SUPERUSER, priority=1, block=True)
unessence_cmd = on_command("取消精华", rule=SUPERUSER, priority=1, block=True)
essence_list_cmd = on_command("精华列表", rule=SUPERUSER, priority=1, block=True)


@essence_cmd.handle()
async def handle_set(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    mid, _ = _target(event, args)
    if mid is None:
        await matcher.send("用法：回复要设为精华的消息并发送 /精华；或 /精华 <message_id> <群号>")
        return
    ok, msg = await set_essence(bot, mid)
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)


@unessence_cmd.handle()
async def handle_unset(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    mid, _ = _target(event, args)
    if mid is None:
        await matcher.send("用法：回复精华消息并发送 /取消精华；或 /取消精华 <message_id>")
        return
    ok, msg = await delete_essence(bot, mid)
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)


@essence_list_cmd.handle()
async def handle_list(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    _, gid = _target(event, args)
    if gid is None:
        nums = [p for p in args.extract_plain_text().split() if p.isdigit()]
        gid = int(nums[0]) if nums else None
    if gid is None:
        await matcher.send("私聊中请带群号：/精华列表 <群号>")
        return
    await matcher.send(await list_essence(bot, gid))


# ---------------------------------------------------------------- 推荐

def can_nominate(group_id: int, message_id: int, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    if message_id in _nominated:
        return False
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    for k in [k for k in _nominate_daily if k[1] != day]:
        _nominate_daily.pop(k, None)
    return _nominate_daily.get((group_id, day), 0) < NOMINATE_DAILY_LIMIT


def mark_nominated(group_id: int, message_id: int, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    _nominated[message_id] = now
    if len(_nominated) > 500:
        for k in sorted(_nominated, key=_nominated.get)[:250]:
            _nominated.pop(k, None)
    key = (group_id, datetime.fromtimestamp(now).strftime("%Y-%m-%d"))
    _nominate_daily[key] = _nominate_daily.get(key, 0) + 1


def nomination_text(group_id: int, message_id: int, who: str, text: str, reason: str) -> str:
    return (
        f"⭐ 精华推荐（群 {group_id}）\n发言人：{who}\n内容：{text[:120]}\n理由：{reason}\n"
        f"同意请发送：/精华 {message_id} {group_id}"
    )
