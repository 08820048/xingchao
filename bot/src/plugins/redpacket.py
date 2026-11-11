"""红包提醒：白名单群里出现 QQ 红包时私聊通知超管。**只提醒，不领取**。

背景：NapCat 没有稳定的"领取红包"接口，自动领取需要改 QQ 客户端底层，封号风险高；而且部分 NapCat 版本
不会把红包消息转成 OneBot 消息段，红包会表现为"空消息"或带 walletElement 的 json 段。
- 识别：消息文本含 "[QQ红包]"，或任一消息段（含 json 段）的内容里出现红包相关特征
- 通知：同一群 60 秒内最多通知一次；开关 kv redpacket_notify（默认开）
- 探测日志：识别到红包时在日志里记录消息段类型，方便确认你的 NapCat 版本到底上报了什么
- 如果你的版本根本不上报红包，这个功能就不会触发——日志里没有"红包"记录即可判断
"""

from __future__ import annotations

import json
import time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger

from src.permission import GROUP_WHITELIST
from src.store import get_store

COOLDOWN = 60.0
_MARKERS = ("walletelement", "qq红包", "[红包]", "红包", "redpacket", "red_packet", "luckymoney")
_last: dict[int, float] = {}


def looks_like_red_packet(raw_message: str, segments: list[tuple[str, dict]]) -> bool:
    text = (raw_message or "").lower()
    if "[qq红包]" in text or "walletelement" in text:
        return True
    for t, data in segments:
        if t == "text":
            continue
        blob = json.dumps(data, ensure_ascii=False).lower()
        if any(m in blob for m in _MARKERS):
            return True
    return False


rp_matcher = on_message(rule=GROUP_WHITELIST, priority=9, block=False)


@rp_matcher.handle()
async def handle_red_packet(bot: Bot, event: GroupMessageEvent) -> None:
    segs = [(s.type, dict(s.data)) for s in event.message]
    if not looks_like_red_packet(event.raw_message, segs):
        return
    logger.info(f"检测到疑似红包：group={event.group_id} user={event.user_id} 消息段类型={[t for t, _ in segs]}")
    if (await get_store().get_kv("redpacket_notify")) == "false":
        return
    now = time.monotonic()
    last = _last.get(event.group_id)
    if last is not None and now - last < COOLDOWN:
        return
    _last[event.group_id] = now
    from src.plugins.appeals import notify_superusers

    await notify_superusers(bot, f"🧧 群 {event.group_id} 里 {event.user_id} 发了一个红包，想要的话快去看看（我不会自动领取）")
