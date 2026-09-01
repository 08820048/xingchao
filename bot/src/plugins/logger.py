"""白名单群全量文本日志：data/logs/group-{group_id}-YYYY-MM-DD.jsonl。

每行：time, group_id, user_id, message_id, raw_plain。
纯图片 / 无文本时 raw_plain 为空字符串，仍记一行。不回消息。

priority=0：必须在所有 block=True 的 matcher（admin/basic/reply）之前运行，
否则指令消息和命中关键词的消息会被 stop_propagation 拦截，无法做到「全量」日志。
"""

from __future__ import annotations

import json
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger

from src.config import get_config
from src.permission import GROUP_WHITELIST
from src.store import get_store

message_logger = on_message(rule=GROUP_WHITELIST, priority=0, block=False)


@message_logger.handle()
async def log_group_message(event: GroupMessageEvent) -> None:
    cfg = get_config()
    now = datetime.now().astimezone()
    day = now.strftime("%Y-%m-%d")
    record = {
        "time": now.isoformat(timespec="seconds"),
        "group_id": event.group_id,
        "user_id": event.user_id,
        "message_id": event.message_id,
        "raw_plain": event.message.extract_plain_text(),
    }
    try:
        log_dir = cfg.xingchao_log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"group-{event.group_id}-{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception(f"写入群消息日志失败：group={event.group_id}")
        return

    # 可选统计（只计数，不存内容）
    try:
        await get_store().incr_msg_stat(event.group_id, day)
    except Exception:
        logger.exception(f"更新 msg_stat 失败：group={event.group_id} day={day}")
