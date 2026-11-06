"""上线 / 下线群通知：让群友知道机器人何时不可用，避免下线后仍有人一直 @ 它。

- 上线：Bot 连接 NapCat 后，若上次下线已公告、或距上次心跳超过 STALE_SECONDS
  （崩溃 / NapCat 掉线 / 长时间重启），向白名单群发「已上线」；短暂的 WS 抖动重连不打扰
- 下线：进程正常关闭（docker stop / 重启 / 部署）时，尽力向白名单群发「暂时下线」
- 局限：NapCat 或 QQ 账号掉线、或进程被强杀时，机器人本身发不出消息，只能在恢复后补发「已上线」
  （含离线时长）；这类掉线请另用 Bot 邮件通知等外部手段监控
- 心跳：后台每 30 秒写入 kv（presence_last_seen），用来估算离线时长
- 开关：kv presence_notify（默认开启，设为 false 关闭）；被临时关闭业务的群不发送
"""

from __future__ import annotations

import asyncio
import time

from nonebot import get_bots, get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from src.permission import disabled_groups, merged_whitelist
from src.store import get_store

STALE_SECONDS = 120  # 心跳间隔超过它，视为发生过一次真实离线
HEARTBEAT_SECONDS = 30
SEND_GAP_SECONDS = 0.8  # 群间发送间隔，降低风控概率
_driver = get_driver()
_heartbeat_task: asyncio.Task | None = None
_announced_offline = False  # 本进程内是否已发过下线公告


async def _enabled() -> bool:
    return (await get_store().get_kv("presence_notify")) != "false"


def _targets() -> list[int]:
    return sorted(merged_whitelist() - disabled_groups())


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时" if hours else f"{days} 天"


async def _broadcast(bot: Bot, text: str) -> int:
    """向各白名单群发送，返回成功数；单群失败不影响其他群。"""
    ok = 0
    for gid in _targets():
        try:
            await bot.call_api("send_group_msg", group_id=gid, message=text)
            ok += 1
        except Exception as e:
            logger.warning(f"上下线通知发送失败：group={gid} {type(e).__name__}: {e}")
        await asyncio.sleep(SEND_GAP_SECONDS)
    return ok


async def _touch() -> None:
    await get_store().set_kv("presence_last_seen", str(int(time.time())))


async def _heartbeat() -> None:
    while True:
        try:
            await _touch()
        except Exception:
            logger.exception("写入心跳失败")
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def _announce_online(bot: Bot) -> None:
    global _announced_offline
    store = get_store()
    await asyncio.sleep(5)  # 等 NapCat 完成登录与群列表同步
    last_seen_raw = await store.get_kv("presence_last_seen")
    announced = (await store.get_kv("presence_offline_announced")) == "1"
    gap = int(time.time()) - int(last_seen_raw) if (last_seen_raw or "").isdigit() else 0
    should = announced or gap > STALE_SECONDS
    await store.set_kv("presence_offline_announced", "0")
    _announced_offline = False
    await _touch()
    if not should or not await _enabled():
        return
    from src.plugins.updates import pending_entry

    if await pending_entry() is not None:
        return  # 本次部署带有更新公告，由更新公告一并告知已上线
    suffix = f"（离线约 {format_duration(gap)}）" if gap > STALE_SECONDS else ""
    sent = await _broadcast(bot, f"✅ 星潮已上线{suffix}，可以继续使用啦～")
    logger.info(f"上线通知已发送：{sent}/{len(_targets())} 个群")


@_driver.on_bot_connect
async def _on_connect(bot: Bot) -> None:
    global _heartbeat_task
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat())
    asyncio.create_task(_announce_online(bot))  # 不阻塞连接流程


@_driver.on_shutdown
async def _on_shutdown() -> None:
    """进程正常关闭：尽力公告下线（总时长限制 8 秒，避免拖慢退出）。"""
    global _announced_offline
    if _announced_offline or not await _enabled():
        return
    bots = [b for b in get_bots().values() if isinstance(b, Bot)]
    if not bots:
        return
    try:
        sent = await asyncio.wait_for(
            _broadcast(bots[0], "⚠️ 星潮正在重启 / 维护，暂时无法回复，稍后恢复～"),
            timeout=8,
        )
    except (TimeoutError, asyncio.TimeoutError):
        sent = 0
    except Exception:
        logger.exception("下线通知异常")
        sent = 0
    if sent:
        _announced_offline = True
        await get_store().set_kv("presence_offline_announced", "1")
    logger.info(f"下线通知已发送：{sent}/{len(_targets())} 个群")
