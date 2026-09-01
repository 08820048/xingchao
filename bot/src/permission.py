"""权限规则：群白名单 / 超管。供各插件的 matcher 复用。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from src.config import get_config, superuser_ids


async def _is_group_whitelisted(event: GroupMessageEvent) -> bool:
    return event.group_id in get_config().xingchao_group_whitelist


async def _is_superuser(event: MessageEvent) -> bool:
    """超管在任意群或私聊可用。"""
    return event.user_id in superuser_ids()


async def _whitelist_or_superuser(event: MessageEvent) -> bool:
    """白名单群任意成员，或超管私聊。普通私聊一律忽略。"""
    if isinstance(event, GroupMessageEvent):
        return event.group_id in get_config().xingchao_group_whitelist
    if isinstance(event, PrivateMessageEvent):
        return event.user_id in superuser_ids()
    return False


# 群聊 matcher 通用规则：必须命中白名单群
GROUP_WHITELIST = Rule(_is_group_whitelisted)

# 超管专用（admin 指令）
SUPERUSER = Rule(_is_superuser)

# 基础指令：白名单群 或 超管私聊
BASIC = Rule(_whitelist_or_superuser)
