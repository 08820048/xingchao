"""群荣誉、群名片、专属头衔。

- /龙王（别名 /群荣誉）：本群龙王、群聊之火等荣誉成员（白名单群内所有人可用）
- /名片 @某人 内容、/头衔 @某人 内容（仅超管，群内使用）；名片需要机器人是管理员，头衔需要是群主
- AI 工具：get_group_honor（所有人）、set_member_card / set_member_title（超管）
"""

from __future__ import annotations

from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER

TITLE_MAX = 6
CARD_MAX = 60
HONOR_LABELS = (
    ("current_talkative", "🐲 龙王"),
    ("performer_list", "🔥 群聊之火"),
    ("legend_list", "🌟 群聊炽焰"),
    ("strong_newbie_list", "🌱 冒尖小春笋"),
    ("emotion_list", "😄 快乐源泉"),
)


def _who(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    name = item.get("nickname") or item.get("user_id") or ""
    extra = item.get("day_count") or item.get("description") or ""
    return f"{name}" + (f"（{extra}{'天' if str(extra).isdigit() else ''}）" if extra else "")


def format_honor(result: Any) -> str:
    """get_group_honor_info 的返回 → 文字；没有数据返回提示。"""
    if not isinstance(result, dict):
        return "暂时没取到群荣誉信息。"
    lines: list[str] = []
    for key, label in HONOR_LABELS:
        val = result.get(key)
        names = [w for w in ([_who(val)] if isinstance(val, dict) else [_who(x) for x in (val or [])[:3]]) if w]
        if names:
            lines.append(f"{label}：{'、'.join(names)}")
    return "\n".join(["🏅 本群荣誉", *lines]) if lines else "本群暂时没有荣誉成员信息。"


async def honor_text(bot: Bot, group_id: int) -> str:
    try:
        return format_honor(await bot.call_api("get_group_honor_info", group_id=group_id, type="all"))
    except Exception as e:
        logger.debug("获取群荣誉失败", exc_info=True)
        return f"获取群荣誉失败：{str(getattr(e, 'info', None) or e)[:60]}"


async def set_card(bot: Bot, group_id: int, user_id: int, card: str) -> tuple[bool, str]:
    card = " ".join(card.split())[:CARD_MAX]
    try:
        await bot.call_api("set_group_card", group_id=group_id, user_id=user_id, card=card)
    except Exception as e:
        return False, f"修改失败：{str(getattr(e, 'info', None) or e)[:60]}（需要机器人是群管理员）"
    return True, f"已把 {user_id} 的群名片改为「{card}」" if card else f"已清除 {user_id} 的群名片"


async def set_title(bot: Bot, group_id: int, user_id: int, title: str) -> tuple[bool, str]:
    title = title.strip()
    if len(title) > TITLE_MAX:
        return False, f"专属头衔最多 {TITLE_MAX} 个字"
    try:
        await bot.call_api("set_group_special_title", group_id=group_id, user_id=user_id,
                           special_title=title, duration=-1)
    except Exception as e:
        return False, f"设置失败：{str(getattr(e, 'info', None) or e)[:60]}（需要机器人是群主）"
    return True, f"已给 {user_id} 设置专属头衔「{title}」" if title else f"已清除 {user_id} 的专属头衔"


def _split(args: Message) -> tuple[int | None, str]:
    """参数 → (被 @ 的 QQ, 其余文字)。"""
    target = next((int(s.data["qq"]) for s in args if s.type == "at" and str(s.data.get("qq", "")).isdigit()), None)
    text = " ".join(s.data.get("text", "") for s in args if s.type == "text").strip()
    return target, text


honor_cmd = on_command("龙王", aliases={"群荣誉"}, rule=GROUP_WHITELIST, priority=5, block=True)
card_cmd = on_command("名片", rule=SUPERUSER, priority=1, block=True)
title_cmd = on_command("头衔", rule=SUPERUSER, priority=1, block=True)


@honor_cmd.handle()
async def handle_honor(matcher: Matcher, bot: Bot, event: GroupMessageEvent) -> None:
    await matcher.send(await honor_text(bot, event.group_id))


@card_cmd.handle()
async def handle_card(matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    target, text = _split(args)
    if target is None:
        await matcher.send("用法：/名片 @某人 新名片（留空则清除）")
        return
    ok, msg = await set_card(bot, event.group_id, target, text)
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)


@title_cmd.handle()
async def handle_title(matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    target, text = _split(args)
    if target is None:
        await matcher.send(f"用法：/头衔 @某人 头衔（最多 {TITLE_MAX} 个字，留空则清除）")
        return
    ok, msg = await set_title(bot, event.group_id, target, text)
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)
