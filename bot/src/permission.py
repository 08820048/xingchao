"""权限规则：群白名单 / 超管。供各插件的 matcher 复用。

白名单 = 环境变量 XINGCHAO_GROUP_WHITELIST（基础）+ 运行时动态增删（SQLite kv 持久化）。
"""

from __future__ import annotations

import json

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.log import logger
from nonebot.rule import Rule

from src.config import get_config, superuser_ids
from src.store import get_store

_RUNTIME_WHITELIST_KEY = "group_whitelist_runtime"
_runtime_whitelist: set[int] = set()


def merged_whitelist() -> set[int]:
    """env 基础白名单 + 运行时动态白名单。"""
    return get_config().xingchao_group_whitelist | _runtime_whitelist


async def load_runtime_whitelist() -> None:
    """启动时从 SQLite 恢复运行时白名单。"""
    global _runtime_whitelist
    raw = await get_store().get_kv(_RUNTIME_WHITELIST_KEY)
    if raw:
        try:
            _runtime_whitelist = {int(g) for g in json.loads(raw)}
            logger.info(f"已恢复运行时群白名单：{sorted(_runtime_whitelist)}")
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.exception("恢复运行时群白名单失败，忽略 kv 数据")


async def add_runtime_group(group_id: int) -> bool:
    """运行时新增白名单群；已存在返回 False。"""
    if group_id in _runtime_whitelist or group_id in get_config().xingchao_group_whitelist:
        return False
    _runtime_whitelist.add(group_id)
    await get_store().set_kv(_RUNTIME_WHITELIST_KEY, json.dumps(sorted(_runtime_whitelist)))
    return True


async def remove_runtime_group(group_id: int) -> bool:
    """运行时移除白名单群；不存在返回 False。"""
    if group_id not in _runtime_whitelist:
        return False
    _runtime_whitelist.discard(group_id)
    await get_store().set_kv(_RUNTIME_WHITELIST_KEY, json.dumps(sorted(_runtime_whitelist)))
    return True


async def _is_group_whitelisted(event: GroupMessageEvent) -> bool:
    return event.group_id in merged_whitelist()


async def _is_superuser(event: MessageEvent) -> bool:
    """超管在任意群或私聊可用。"""
    return event.user_id in superuser_ids()


async def _whitelist_or_superuser(event: MessageEvent) -> bool:
    """白名单群任意成员，或超管私聊。普通私聊一律忽略。"""
    if isinstance(event, GroupMessageEvent):
        return event.group_id in merged_whitelist()
    if isinstance(event, PrivateMessageEvent):
        return event.user_id in superuser_ids()
    return False


# 启动时恢复运行时白名单
get_driver().on_startup(load_runtime_whitelist)

# 群聊 matcher 通用规则：必须命中白名单群（env + 运行时合并）
GROUP_WHITELIST = Rule(_is_group_whitelisted)

# 超管专用（admin 指令）
SUPERUSER = Rule(_is_superuser)

# 基础指令：白名单群 或 超管私聊
BASIC = Rule(_whitelist_or_superuser)
