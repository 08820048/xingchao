"""群友对机器人发言的表情反馈：被点赞、被点"？"都是信号，机器人据此收敛或放开。

- 追踪机器人自己发出的群消息（awareness 的 API 钩子里登记 message_id）
- 监听 NapCat 的 group_msg_emoji_like 通知；别人给机器人的消息贴表情时记录一次反馈
- 用法：
  1) 近 30 分钟内收到 ≥2 次负面反馈 → 暂停主动发言 30 分钟（接话窗口不受影响）
  2) 把最近的反馈概况写进主动发言的提示词，让模型自己校准
- 全部在内存里，重启清空；只记 emoji 与计数，不记是谁点的
- 注意：通知字段按 NapCat 文档约定解析（notice_type/message_id/likes/is_add），
  若你的版本字段不同，这里会静默忽略，不影响其他功能
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from nonebot import on_notice
from nonebot.log import logger

# 系统表情 ID → 名称（只列常见的，用于提示词展示）
NAMES = {
    76: "赞", 66: "爱心", 124: "OK", 182: "笑哭", 320: "庆祝", 99: "鼓掌", 78: "握手", 271: "吃瓜",
    299: "牛啊", 175: "卖萌", 319: "比心", 315: "加油", 21: "可爱", 14: "微笑", 13: "呲牙",
    178: "斜眼笑", 201: "点赞", 63: "玫瑰",
    77: "踩", 105: "鄙视", 22: "白眼", 121: "差劲", 265: "辣眼睛", 262: "脑阔疼", 268: "问号", 32: "疑问",
}
POSITIVE = {76, 66, 124, 182, 320, 99, 78, 271, 299, 175, 319, 315, 21, 14, 13, 178, 201, 63,
            128077, 128514, 127881, 128079, 128293, 10084}
NEGATIVE = {77, 105, 22, 121, 265, 262, 268, 32}

DAMPEN_THRESHOLD = 2
DAMPEN_WINDOW = 1800      # 统计负面反馈的窗口（秒）
DAMPEN_SECONDS = 1800     # 暂停主动发言多久
TRACK_MAX = 300
EVENTS_MAX = 100

_bot_msgs: dict[int, tuple[int, float, str]] = {}   # message_id → (群号, 时间, 文本前 30 字)
_events: dict[int, deque] = {}                      # 群号 → deque[(ts, 极性, emoji_id)]
_dampen_until: dict[int, float] = {}


def polarity(emoji_id: int) -> str:
    if emoji_id in NEGATIVE:
        return "negative"
    return "positive" if emoji_id in POSITIVE else "neutral"


def track_bot_message(message_id: Any, group_id: int, text: str, now: float | None = None) -> None:
    """登记机器人发出的群消息（之后别人给它贴表情才认得出来）。"""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return
    _bot_msgs[mid] = (group_id, now if now is not None else time.time(), text[:30])
    while len(_bot_msgs) > TRACK_MAX:
        _bot_msgs.pop(next(iter(_bot_msgs)))


def record_reaction(
    message_id: int, emoji_ids: list[int], *, now: float | None = None,
) -> str | None:
    """记录一次对机器人消息的反馈；返回整体极性（不是机器人的消息返回 None）。"""
    info = _bot_msgs.get(message_id)
    if not info:
        return None
    now = now if now is not None else time.time()
    gid = info[0]
    q = _events.setdefault(gid, deque(maxlen=EVENTS_MAX))
    result = "neutral"
    for eid in emoji_ids:
        pol = polarity(eid)
        q.append((now, pol, eid))
        if pol == "negative":
            result = "negative"
        elif pol == "positive" and result != "negative":
            result = "positive"
    negatives = sum(1 for t, p, _ in q if p == "negative" and now - t <= DAMPEN_WINDOW)
    if negatives >= DAMPEN_THRESHOLD:
        _dampen_until[gid] = now + DAMPEN_SECONDS
    return result


def dampened(group_id: int, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    return now < _dampen_until.get(group_id, 0.0)


def prompt_line(group_id: int, now: float | None = None, window: float = 3600) -> str:
    """给主动发言提示词的反馈概况；最近没有反馈返回空串。"""
    now = now if now is not None else time.time()
    recent = [(p, e) for t, p, e in _events.get(group_id, ()) if now - t <= window]
    if not recent:
        return ""
    pos = [NAMES.get(e, str(e)) for p, e in recent if p == "positive"]
    neg = [NAMES.get(e, str(e)) for p, e in recent if p == "negative"]
    parts = []
    if pos:
        parts.append(f"正面 {len(pos)} 次（{'、'.join(dict.fromkeys(pos))}）")
    if neg:
        parts.append(f"负面 {len(neg)} 次（{'、'.join(dict.fromkeys(neg))}）")
    if not parts:
        return ""
    tip = "负面偏多时请收敛、少插话；正面多说明大家喜欢你这样的方式。"
    return f"最近一小时群友对你发言的表情反应：{'；'.join(parts)}。{tip}"


def reset() -> None:
    _bot_msgs.clear()
    _events.clear()
    _dampen_until.clear()


# ---------------------------------------------------------------- 事件接入

def _parse_likes(event: Any) -> list[int]:
    ids: list[int] = []
    for item in getattr(event, "likes", None) or []:
        raw = item.get("emoji_id") if isinstance(item, dict) else item
        if str(raw).isdigit():
            ids.append(int(raw))
    return ids


async def _is_emoji_like(event) -> bool:
    return getattr(event, "notice_type", "") == "group_msg_emoji_like"


emoji_like_notice = on_notice(rule=_is_emoji_like, priority=0, block=False)


@emoji_like_notice.handle()
async def handle_emoji_like(event) -> None:
    try:
        if getattr(event, "is_add", True) is False:
            return  # 取消表情不计
        mid = getattr(event, "message_id", None)
        if mid is None:
            return
        pol = record_reaction(int(mid), _parse_likes(event))
        if pol:
            logger.debug(f"机器人消息 {mid} 收到{pol}反馈")
    except Exception:
        logger.debug("处理表情反馈失败", exc_info=True)
