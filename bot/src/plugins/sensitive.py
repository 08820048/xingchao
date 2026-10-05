"""敏感词监控：命中即撤回，可选禁言与通知超管（防广告/敏感信息）。

- 监听白名单群消息（priority=3，命中后 stop_propagation，不再触发 AI/关键词回复）
- 配置分全局默认 + 每群覆盖（面板「敏感词」页可改，持久化 kv，即时生效）
- 动作：撤回原消息；mute_minutes > 0 时追加禁言；notify 开启时通知超管
- 撤回失败（机器人非群管理员等）会如实转述给超管
"""

from __future__ import annotations

import json
import re
from typing import Any

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER
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


# ---------------------------------------------------------------- 词库读写

MAX_WORDS_CHARS = 5000  # 与面板上限一致


def clean_words(items: list[str]) -> list[str]:
    """去空白、去空项、按大小写不敏感去重（保持原顺序与原始大小写）。"""
    words: list[str] = []
    seen: set[str] = set()
    for item in items:
        w = str(item).strip()
        if w and w.casefold() not in seen:
            seen.add(w.casefold())
            words.append(w)
    return words


async def get_words() -> list[str]:
    """当前全局敏感词列表。"""
    raw = str(await _kv("sensitive_words") or "")
    return clean_words(raw.split(","))


async def add_words(items: list[str]) -> tuple[list[str], list[str]]:
    """批量添加，返回（新增, 已存在）。超长则抛 ValueError 且不落库。"""
    requested = clean_words(items)
    current = await get_words()
    existing_keys = {w.casefold() for w in current}
    added, existing = [], []
    for w in requested:
        if w.casefold() in existing_keys:
            existing.append(w)
        else:
            added.append(w)
            existing_keys.add(w.casefold())
    merged = current + added
    text = ",".join(merged)
    if len(text) > MAX_WORDS_CHARS:
        raise ValueError(
            f"词库过长（上限 {MAX_WORDS_CHARS} 字），本次未修改；"
            f"当前 {len(current)} 个词，请先移除部分再添加"
        )
    if added:
        await get_store().set_kv("sensitive_words", text)
    return added, existing


async def remove_words(items: list[str]) -> tuple[list[str], list[str]]:
    """批量移除（大小写不敏感），返回（已移除, 不在词库中）。"""
    requested = clean_words(items)
    current = await get_words()
    keys = {w.casefold() for w in requested}
    current_keys = {w.casefold() for w in current}
    removed = [w for w in current if w.casefold() in keys]
    missing = [w for w in requested if w.casefold() not in current_keys]
    if removed:
        await get_store().set_kv(
            "sensitive_words", ",".join(w for w in current if w.casefold() not in keys)
        )
    return removed, missing


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

    # 群内 @ 被处罚者并说明原因，避免莫名其妙被禁言
    if recalled or muted:
        reason = (
            "你因发送了敏感违禁词被禁言，请记得阅读并遵守群规！"
            if muted
            else "你因发送了敏感违禁词，消息已被撤回，请记得阅读并遵守群规！"
        )
        try:
            await bot.call_api(
                "send_group_msg",
                group_id=event.group_id,
                message=MessageSegment.at(event.user_id) + " " + reason,
            )
        except Exception:
            logger.exception(f"发送敏感词处罚提示失败：group={event.group_id}")

    if cfg["sensitive_notify"]:
        status = "已撤回" if recalled else f"撤回失败：{recall_err}"
        extra = "，并已禁言" if muted else ""
        await _notify(
            bot,
            f"🚨 敏感词告警\n群号: {event.group_id}\nQQ: {event.user_id}\n"
            f"命中词: {hit}\n处理: {status}{extra}\n"
            f"内容: {event.message.extract_plain_text()[:100]}",
        )


# ---------------------------------------------------------------- 指令：/敏感词

sensitive_admin = on_command(
    "敏感词", aliases={"sensitive"}, rule=SUPERUSER, priority=1, block=True
)

_USAGE = (
    "用法：\n"
    "/敏感词 add <词1> [词2] … — 批量添加（空格 / 逗号分隔）\n"
    "/敏感词 del <词1> [词2] … — 批量移除\n"
    "/敏感词 list — 查看词库"
)
_ADD_ACTIONS = {"add", "添加", "增加"}
_DEL_ACTIONS = {"del", "delete", "remove", "删除", "移除"}
_LIST_ACTIONS = {"list", "列表", "查看"}


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


def _brief(words: list[str], limit: int = 600) -> str:
    """把词列表拼成一行，过长时截断（防止 QQ 消息刷屏）。"""
    text = ""
    for w in words:
        piece = w if not text else "、" + w
        if len(text) + len(piece) > limit:
            return f"{text}……（共 {len(words)} 个）"
        text += piece
    return text


@sensitive_admin.handle()
async def handle_sensitive_admin(args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    words = clean_words(re.split(r"[\s,，、;；]+", rest))

    if action in _LIST_ACTIONS or not action:
        current = await get_words()
        cfg = await sensitive_config()
        state = "开启" if cfg["sensitive_enabled"] else "关闭"
        if not current:
            await _send(sensitive_admin, f"敏感词词库为空（监控开关：{state}）。\n{_USAGE}")
            return
        await _send(
            sensitive_admin,
            f"敏感词词库（全局，共 {len(current)} 个，监控：{state}）：\n{_brief(current)}",
        )
        return

    if action in _ADD_ACTIONS:
        if not words:
            await _send(sensitive_admin, _USAGE)
            return
        try:
            added, existing = await add_words(words)
        except ValueError as e:
            await _send(sensitive_admin, f"添加失败：{e}。")
            return
        total = len(await get_words())
        lines = []
        if added:
            lines.append(f"已添加 {len(added)} 个：{_brief(added)}")
        if existing:
            lines.append(f"已存在 {len(existing)} 个（跳过）：{_brief(existing)}")
        lines.append(f"词库共 {total} 个。")
        if added and not (await sensitive_config())["sensitive_enabled"]:
            lines.append("⚠️ 敏感词监控当前未开启，词库暂不生效（可在面板「敏感词」页开启）。")
        await _send(sensitive_admin, "\n".join(lines))
        return

    if action in _DEL_ACTIONS:
        if not words:
            await _send(sensitive_admin, _USAGE)
            return
        removed, missing = await remove_words(words)
        total = len(await get_words())
        lines = []
        if removed:
            lines.append(f"已移除 {len(removed)} 个：{_brief(removed)}")
        if missing:
            lines.append(f"不在词库中 {len(missing)} 个：{_brief(missing)}")
        lines.append(f"词库共 {total} 个。")
        await _send(sensitive_admin, "\n".join(lines))
        return

    await _send(sensitive_admin, _USAGE)
