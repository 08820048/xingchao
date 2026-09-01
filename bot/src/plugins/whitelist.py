"""白名单守卫：对非白名单群消息只打 debug 日志，不做任何业务与回复。

各插件的 matcher 均通过 src.permission 的规则先过白名单；
本插件仅负责可观测性（ignore 记录）。
"""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger

from src.permission import merged_whitelist

ignore_logger = on_message(priority=2, block=False)


@ignore_logger.handle()
async def handle_ignore(event: GroupMessageEvent) -> None:
    if event.group_id not in merged_whitelist():
        logger.debug(f"忽略非白名单群消息：group={event.group_id} user={event.user_id}")
