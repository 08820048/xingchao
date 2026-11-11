"""动态在线状态：把机器人的 QQ 自定义在线状态跟时间与群里的热闹程度联动，更有"活人感"。

- 默认关闭（会改动机器人账号的在线状态展示）：/plugin online on|off，kv online_status_enabled
- 每 30 分钟按 时段 + 最近 10 分钟全部群的消息量 选一句状态文字，变化了才更新
- 调用 NapCat set_diy_online_status；该接口参数在不同版本里可能有差异，失败只记日志
"""

from __future__ import annotations

import time
from datetime import datetime

from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from src.background import periodic
from src.store import get_store

SCAN_SECONDS = 1800
BUSY_THRESHOLD = 20  # 最近 10 分钟全部群消息数 ≥ 这个值算"热闹"
_last_wording = ""


def pick_wording(now: datetime, recent_messages: int) -> str:
    h = now.hour
    if h < 6 or h >= 23:
        return "夜深了·陪聊中"
    if h < 9:
        return "刚醒·加载中"
    if 12 <= h < 14:
        return "午饭时间"
    if recent_messages >= BUSY_THRESHOLD:
        return "群里热闹·吃瓜"
    if h < 18:
        return "潜水观察中"
    return "在线·陪聊中"


def recent_activity(seconds: float = 600) -> int:
    from src.plugins import awareness

    now = time.time()
    return sum(
        1 for buf in awareness._buffers.values() for e in buf if not e["bot"] and now - e["ts"] <= seconds
    )


async def update(bot, now: datetime | None = None) -> str | None:
    """更新一次状态；返回新文案（没变化/未开启/失败返回 None）。"""
    global _last_wording
    if await get_store().get_kv("online_status_enabled") != "true":
        return None
    wording = pick_wording(now or datetime.now(), recent_activity())
    if wording == _last_wording:
        return None
    try:
        await bot.call_api("set_diy_online_status", face_id="0", face_type="0", wording=wording)
    except Exception:
        logger.warning("设置自定义在线状态失败（NapCat 版本可能不支持该接口）", exc_info=True)
        return None
    _last_wording = wording
    return wording


@periodic("在线状态更新", SCAN_SECONDS)
async def _scan(bot: Bot) -> None:
    await update(bot)
