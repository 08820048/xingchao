"""群签到：机器人账号每天在各白名单群自动签到一次（NapCat set_group_sign）。

- 每个群每天的签到时间在 08:00–10:00 之间按 (群号, 日期) 稳定散列，错开不同群、也不是整点
- 每个群每天只尝试一次（成功与否都记录，避免反复调用）；失败只记日志
- 开关 kv group_sign_enabled（默认开）：/plugin sign on|off
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime

from nonebot import get_bots, get_driver
from nonebot.log import logger

from src.permission import disabled_groups, merged_whitelist
from src.store import get_store

SCAN_SECONDS = 60
_worker_started = False


def sign_minute(group_id: int, day: str) -> int:
    """当天该群的签到时刻（从 00:00 起的分钟数，落在 08:00–10:00）。"""
    h = int(hashlib.md5(f"{group_id}:{day}".encode()).hexdigest()[:6], 16)
    return 8 * 60 + h % 120


def is_due(group_id: int, now: datetime) -> bool:
    return now.hour * 60 + now.minute >= sign_minute(group_id, now.strftime("%Y-%m-%d"))


async def sign_group(bot, group_id: int) -> bool:
    for api in ("set_group_sign", "send_group_sign"):
        try:
            await bot.call_api(api, group_id=group_id)
            return True
        except Exception:
            logger.debug(f"{api} 失败", exc_info=True)
    return False


async def tick(bot, now: datetime | None = None) -> list[int]:
    """扫描一轮，返回本轮完成签到的群。"""
    now = now or datetime.now()
    store = get_store()
    if await store.get_kv("group_sign_enabled") == "false":
        return []
    day = now.strftime("%Y-%m-%d")
    done: list[int] = []
    for gid in sorted(merged_whitelist() - disabled_groups()):
        if not is_due(gid, now) or await store.get_kv(f"group_sign_last_{gid}") == day:
            continue
        await store.set_kv(f"group_sign_last_{gid}", day)  # 先记录：一天只试一次
        if await sign_group(bot, gid):
            done.append(gid)
            logger.info(f"群 {gid} 已自动签到")
        else:
            logger.warning(f"群 {gid} 自动签到失败（接口不支持或已签到）")
    return done


async def _worker() -> None:
    while True:
        try:
            bot = next(iter(get_bots().values()), None)
            if bot is not None:
                await tick(bot)
        except Exception:
            logger.exception("群签到扫描异常")
        await asyncio.sleep(SCAN_SECONDS)


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())
