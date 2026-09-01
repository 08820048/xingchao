"""敏感词监控：命中即撤回，可选禁言与通知超管（防广告/敏感信息）。

- 监听白名单群消息（priority=3，命中后 stop_propagation，不再触发 AI/关键词回复）
- 配置分全局默认 + 每群覆盖（面板「敏感词」页可改，持久化 kv，即时生效）
- 动作：撤回原消息；mute_minutes > 0 时追加禁言；notify 开启时通知超管
- 撤回失败（机器人非群管理员等）会如实转述给超管
"""

from __future__ import annotations

import json
from typing import Any

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger

from src.permission import GROUP_WHITELIST
from src.store import get_store

DEFAULTS: dict[str, Any] = {
    "sensitive_enabled": False,  # 默认关闭，需在面板开启
    "sensitive_words": "",  # 逗号分隔
    "sensitive_mute_minutes": 0,  # 命中后禁言分钟数，0 = 不禁言
    "sensitive_notify": True,  # 命中后通知超管
}


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


async def _group_overrides() -> dict[int, dict[str, Any]]:
    raw = await get_store().get_kv("sensitive_group_config")
    if not raw:
        return {}
    try:
        return {int(g): v for g, v in json.loads(raw).items()}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


async def save_group_override(group_id: int, fields: dict[str, Any]) -> None:
    overrides = await _group_overrides()
    overrides[group_id] = fields
    await get_store().set_kv("sensitive_group_config", json.dumps(overrides, ensure_ascii=False))


async def clear_group_override(group_id: int) -> bool:
    overrides = await _group_overrides()
    if group_id not in overrides:
        return False
    overrides.pop(group_id)
    await get_store().set_kv("sensitive_group_config", json.dumps(overrides, ensure_ascii=False))
    return True


async def sensitive_config(group_id: int | None = None) -> dict[str, Any]:
    """生效配置 = 全局默认 + 该群覆盖。"""
    global_cfg = {k: await _kv(k) for k in DEFAULTS}
    if group_id is None:
        return global_cfg
    override = (await _group_overrides()).get(group_id, {})
    merged = {**global_cfg, **{k: v for k, v in override.items() if k in DEFAULTS}}
    lr = merged["sensitive_enabled"]
    merged["sensitive_enabled"] = lr is True or (isinstance(lr, str) and lr != "false")
    merged["sensitive_notify"] = (
        merged["sensitive_notify"] is True
        or (isinstance(merged["sensitive_notify"], str) and merged["sensitive_notify"] != "false")
    )
    merged["sensitive_mute_minutes"] = int(merged["sensitive_mute_minutes"] or 0)
    return merged


# ---------------------------------------------------------------- 处理

sensitive_matcher = on_message(rule=GROUP_WHITELIST, priority=3, block=False)


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def _notify(bot: Bot, text: str) -> None:
    for uid in _superuser_ids():
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"敏感词通知超管 {uid} 失败")


@sensitive_matcher.handle()
async def handle_sensitive(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id):
        return
    cfg = await sensitive_config(event.group_id)
    if not cfg["sensitive_enabled"] or not str(cfg["sensitive_words"]).strip():
        return
    text = event.message.extract_plain_text().strip().lower()
    if not text:
        return

    hit: str | None = None
    for w in str(cfg["sensitive_words"]).split(","):
        w = w.strip().lower()
        if w and w in text:
            hit = w
            break
    if not hit:
        return

    logger.warning(f"敏感词命中：group={event.group_id} user={event.user_id} 词={hit}")
    matcher.stop_propagation()  # 命中后不再触发 AI / 关键词回复

    # 撤回
    recalled, recall_err = False, ""
    try:
        await bot.call_api("delete_msg", message_id=event.message_id)
        recalled = True
    except Exception as e:
        recall_err = str(e)
        logger.warning(f"敏感词撤回失败：{e}")

    # 禁言（可选）
    muted = False
    if cfg["sensitive_mute_minutes"] > 0:
        try:
            await bot.call_api(
                "set_group_ban",
                group_id=event.group_id,
                user_id=event.user_id,
                duration=min(cfg["sensitive_mute_minutes"] * 60, 30 * 24 * 3600),
            )
            muted = True
        except Exception:
            logger.exception("敏感词禁言失败")

    if cfg["sensitive_notify"]:
        status = "已撤回" if recalled else f"撤回失败：{recall_err}"
        extra = "，并已禁言" if muted else ""
        await _notify(
            bot,
            f"🚨 敏感词告警\n群号: {event.group_id}\nQQ: {event.user_id}\n"
            f"命中词: {hit}\n处理: {status}{extra}\n"
            f"内容: {event.message.extract_plain_text()[:100]}",
        )
