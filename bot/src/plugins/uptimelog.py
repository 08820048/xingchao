"""记录 QQ 上线/掉线时间与每次在线时长，供面板和日志排查「固定多久掉一次」。

- 掉线：监听 NapCat 的 `bot_offline` notice，写入 SQLite `bot_events`，并追加
  `data/logs/bot-uptime-YYYY-MM-DD.jsonl`；duration_seconds = 本次从上线到掉线的时长
- 上线：bot 连上 NapCat 时写一条 online 事件，duration_seconds 先记 0
- 面板：`/panel/api/uptime` 读取最近事件与近 7 天摘要
- 只记录连接事件，不记录聊天内容
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from nonebot import get_driver, on_notice
from nonebot.adapters.onebot.v11 import Bot, NoticeEvent
from nonebot.log import logger

from src.config import get_config
from src.store import get_store

_driver = get_driver()
_last_offline_monotonic = 0.0


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _append_file(record: dict) -> None:
    try:
        log_dir = get_config().xingchao_log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        path = log_dir / f"bot-uptime-{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("写入 bot-uptime 日志失败")


async def _record(event: str, detail: str = "", duration_seconds: int = 0) -> None:
    store = get_store()
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    await store.add_bot_event(
        event, detail=detail, duration_seconds=duration_seconds, ts=now_iso
    )
    _append_file(
        {
            "time": now_iso,
            "event": event,
            "detail": detail,
            "duration_seconds": max(0, int(duration_seconds)),
        }
    )


async def _wait_qq_online(bot: Bot, timeout: int = 30) -> bool:
    """WebSocket 连上不等于 QQ 在线；用 get_status 确认，避免把断线中的账号记成上线。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = await bot.call_api("get_status")
            if isinstance(data, dict) and data.get("online"):
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


@_driver.on_bot_connect
async def _on_connect(bot: Bot) -> None:
    async def _record_when_online() -> None:
        try:
            if not await _wait_qq_online(bot):
                logger.warning("bot 已连接 NapCat，但 QQ 仍处于离线状态，暂不记录上线事件")
                return
            await _record("online", f"QQ {bot.self_id} 已连接")
            logger.info("已记录上线事件")
        except Exception:
            logger.exception("记录上线事件失败")

    asyncio.create_task(_record_when_online())


offline_notice = on_notice(priority=0, block=False)


@offline_notice.handle()
async def _on_offline(event: NoticeEvent) -> None:
    global _last_offline_monotonic
    if getattr(event, "notice_type", "") != "bot_offline":
        return
    now = time.monotonic()
    if now - _last_offline_monotonic < 30:
        return  # 同一次掉线可能被重复上报
    _last_offline_monotonic = now

    store = get_store()
    detail_parts = [
        str(getattr(event, "tag", "") or "").strip(),
        str(getattr(event, "message", "") or "").strip(),
    ]
    detail = "：".join(part for part in detail_parts if part)[:300]
    duration = 0
    try:
        last_any = await store.last_bot_event()
        if last_any and last_any["event"] == "online":
            started = _parse_ts(last_any["ts"])
            if started is not None:
                duration = max(0, int(time.time() - started))
        await _record("offline", detail, duration)
        logger.warning(f"已记录掉线事件，本次在线约 {duration} 秒：{detail}")
    except Exception:
        logger.exception("记录掉线事件失败")
