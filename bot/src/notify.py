"""超管私聊通知：只发给好友 / 指定白名单，失败后 1 小时内不再重试。

QQ 对「机器人频繁私聊非好友」很敏感，失败调用本身也可能放大风控。这里统一收口：
- 默认只发给「是机器人好友」的超管；
- kv `notify_superusers_allowlist` 可显式指定通知对象（逗号分隔 QQ 号），设置后不再做好友过滤；
- 发送失败的 QQ 号进入 1 小时冷却，避免每次告警都重复撞同一个失败目标；
- 好友列表按 bot 缓存 10 分钟，避免每条通知都调用一次 get_friend_list。
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from src.store import get_store

_ALLOWLIST_KEY = "notify_superusers_allowlist"
_FRIEND_TTL = 600.0
_FAIL_TTL = 3600.0

_friend_cache: dict[str, tuple[float, set[int]]] = {}
_failed_until: dict[tuple[str, int], float] = {}


async def _allowlist() -> set[int] | None:
    """返回显式通知白名单；未设置/为空/为 all 时返回 None（走好友过滤）。"""
    raw = await get_store().get_kv(_ALLOWLIST_KEY)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw.lower() in ("all", "*"):
        return None
    ids: set[int] = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning(f"忽略非法通知白名单项：{part!r}")
    return ids or None


async def _friend_ids(bot: Bot) -> set[int] | None:
    """机器人好友 QQ 集合；接口不可用时返回 None（调用方决定是否回退）。"""
    self_id = str(bot.self_id)
    now = time.monotonic()
    cached = _friend_cache.get(self_id)
    if cached is not None and now - cached[0] < _FRIEND_TTL:
        return cached[1]
    try:
        data = await bot.call_api("get_friend_list")
    except Exception as e:
        logger.debug(f"获取好友列表失败，本次不做好友过滤：{type(e).__name__}: {e}")
        return None
    if not isinstance(data, list):
        logger.debug(f"好友列表返回格式异常：{type(data).__name__}")
        return None
    ids: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        uid = item.get("user_id")
        try:
            ids.add(int(uid))
        except (TypeError, ValueError):
            continue
    _friend_cache[self_id] = (now, ids)
    return ids


async def notify_targets(
    bot: Bot,
    target_ids: Iterable[int],
    text: str,
    *,
    exclude: int | None = None,
    kind: str = "超管通知",
) -> None:
    """按好友/白名单过滤后逐个私聊，失败目标 1 小时内跳过。"""
    allow = await _allowlist()
    if allow is not None:
        targets = set(allow)
    else:
        targets = {int(uid) for uid in target_ids}
        friends = await _friend_ids(bot)
        if friends is not None:
            targets &= friends
    if exclude is not None:
        targets.discard(exclude)

    now = time.monotonic()
    self_id = str(bot.self_id)
    for uid in sorted(targets):
        key = (self_id, uid)
        until = _failed_until.get(key, 0.0)
        if until > now:
            logger.debug(f"{kind} {uid} 仍在失败冷却中（{int(until - now)} 秒），跳过")
            continue
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception as e:
            _failed_until[key] = now + _FAIL_TTL
            logger.warning(f"{kind} {uid} 发送失败，1 小时内不再重试：{type(e).__name__}: {e}")
        else:
            _failed_until.pop(key, None)


def superuser_ids() -> set[int]:
    """当前生效超管（包含运行时添加的超管，由 permission.sync_effective_superusers 同步）。"""
    return {int(uid) for uid in get_driver().config.superusers}
