"""群管功能（仅超管）：/mute /unmute /banall /kick /recall + 新人进群欢迎。

- 目标用户通过 @提及 指定（/mute @某人 10）
- /recall 可回复目标消息后发送，或直接带 message_id
- 机器人小号必须是群管理员，API 失败（权限不足等）时友好提示，不抛崩
- 新人进群欢迎（白名单群），可通过 /plugin welcome on|off 开关（持久化 kv）
"""

from __future__ import annotations

from typing import Optional

from nonebot import on_command, on_notice
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER
from src.store import get_store

MAX_BAN_SECONDS = 30 * 24 * 3600  # OneBot v11 上限 30 天

mute_cmd = on_command("mute", rule=SUPERUSER, priority=1, block=True)
unmute_cmd = on_command("unmute", rule=SUPERUSER, priority=1, block=True)
banall_cmd = on_command("banall", rule=SUPERUSER, priority=1, block=True)
kick_cmd = on_command("kick", rule=SUPERUSER, priority=1, block=True)
recall_cmd = on_command("recall", rule=SUPERUSER, priority=1, block=True)
welcome_cmd = on_command("welcome", rule=SUPERUSER, priority=1, block=True)

welcome_notice = on_notice(rule=GROUP_WHITELIST, priority=2, block=False)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


def _target_from_args(args: Message) -> Optional[int]:
    """从命令参数中取第一个 @提及 的 QQ 号。"""
    for seg in args:
        if seg.type == "at" and str(seg.data.get("qq", "")).isdigit():
            return int(seg.data["qq"])
    return None


def _duration_from_args(args: Message) -> int:
    """从命令参数文本取分钟数，默认 10 分钟，范围 [1, 43200]。"""
    text = args.extract_plain_text().strip()
    for token in text.replace("，", " ").split():
        if token.isdigit():
            return max(1, min(int(token) * 60, MAX_BAN_SECONDS))
    return 10 * 60


async def _guard_group(event: MessageEvent, matcher: Matcher) -> Optional[GroupMessageEvent]:
    if not isinstance(event, GroupMessageEvent):
        await _send(matcher, "该指令只能在群聊中使用。")
        return None
    return event


async def _call(matcher: Matcher, coro) -> None:
    """调用管理 API，失败（权限不足等）给友好提示。"""
    try:
        await coro
    except MatcherException:
        raise
    except Exception as e:
        logger.exception("群管 API 调用失败")
        await _send(matcher, f"操作失败：{e}（请确认机器人是群管理员）")


@mute_cmd.handle()
async def handle_mute(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    group = await _guard_group(event, matcher)
    if group is None:
        return
    target = _target_from_args(args)
    if target is None:
        await _send(mute_cmd, "用法：/mute @某人 [分钟数，默认 10]")
        return
    duration = _duration_from_args(args)
    await _call(
        matcher,
        bot.call_api(
            "set_group_ban",
            group_id=group.group_id,
            user_id=target,
            duration=duration,
        ),
    )
    await _send(mute_cmd, f"已禁言 {target} {duration // 60} 分钟。")


@unmute_cmd.handle()
async def handle_unmute(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    group = await _guard_group(event, matcher)
    if group is None:
        return
    target = _target_from_args(args)
    if target is None:
        await _send(unmute_cmd, "用法：/unmute @某人")
        return
    await _call(
        matcher,
        bot.call_api(
            "set_group_ban", group_id=group.group_id, user_id=target, duration=0
        ),
    )
    await _send(unmute_cmd, f"已解除 {target} 的禁言。")


@banall_cmd.handle()
async def handle_banall(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    group = await _guard_group(event, matcher)
    if group is None:
        return
    action = args.extract_plain_text().strip().lower()
    if action not in ("on", "off"):
        await _send(banall_cmd, "用法：/banall on 或 /banall off")
        return
    enable = action == "on"
    await _call(
        matcher,
        bot.call_api("set_group_whole_ban", group_id=group.group_id, enable=enable),
    )
    await _send(banall_cmd, "已开启全体禁言。" if enable else "已关闭全体禁言。")


@kick_cmd.handle()
async def handle_kick(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    group = await _guard_group(event, matcher)
    if group is None:
        return
    target = _target_from_args(args)
    if target is None:
        await _send(kick_cmd, "用法：/kick @某人")
        return
    await _call(
        matcher,
        bot.call_api("set_group_kick", group_id=group.group_id, user_id=target),
    )
    await _send(kick_cmd, f"已将 {target} 移出群聊。")


@recall_cmd.handle()
async def handle_recall(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    """回复目标消息后发 /recall，或 /recall <message_id>。"""
    message_id: Optional[int] = None
    if event.reply is not None:
        message_id = event.reply.message_id
    else:
        text = args.extract_plain_text().strip()
        if text.isdigit():
            message_id = int(text)
    if message_id is None:
        await _send(recall_cmd, "用法：回复目标消息发送 /recall，或 /recall <message_id>")
        return
    await _call(matcher, bot.call_api("delete_msg", message_id=message_id))
    await _send(recall_cmd, f"已撤回消息 {message_id}。")


@welcome_cmd.handle()
async def handle_welcome(matcher: Matcher, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    action, _, rest = raw.partition(" ")
    if action == "on" or action == "off":
        try:
            await get_store().set_kv("welcome_enabled", "true" if action == "on" else "false")
        except Exception:
            logger.exception("写入 welcome_enabled 开关失败")
        await _send(welcome_cmd, f"新人进群欢迎已{'开启' if action == 'on' else '关闭'}。")
    elif action == "view":
        await _send(welcome_cmd, f"当前欢迎语：\n{await get_welcome_text()}")
    elif action == "set" and rest.strip():
        text = rest.strip()
        if len(text) > 1000:
            await _send(welcome_cmd, "欢迎语过长（上限 1000 字）。")
            return
        try:
            await get_store().set_kv("welcome_text", text)
        except Exception:
            logger.exception("写入 welcome_text 失败")
            return
        await _send(welcome_cmd, f"欢迎语已更新：\n{text}")
    else:
        await _send(
            welcome_cmd,
            "用法：/welcome on|off、/welcome view、/welcome set <欢迎语>\n"
            "占位符：{at}=@新人，{qq}=新人QQ，{group}=群号",
        )


DEFAULT_WELCOME_TEXT = "欢迎进群～发送 /help 查看我能做什么。"


async def is_welcome_enabled() -> bool:
    value = await get_store().get_kv("welcome_enabled")
    return value != "false"  # 默认开启


async def get_welcome_text() -> str:
    value = await get_store().get_kv("welcome_text")
    return value if value else DEFAULT_WELCOME_TEXT


def render_welcome(text: str, user_id: int, group_id: int) -> Message:
    """渲染欢迎语：{at} = @新人，{qq} = 新人 QQ，{group} = 群号。"""
    msg = Message()
    for i, part in enumerate(text.split("{at}")):
        if i:
            msg += MessageSegment.at(user_id)
        msg += part.replace("{qq}", str(user_id)).replace("{group}", str(group_id))
    return msg


@welcome_notice.handle()
async def handle_welcome_notice(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    if event.user_id == int(bot.self_id):
        return  # 机器人自己进群不欢迎
    if not await is_welcome_enabled():
        return
    message = render_welcome(await get_welcome_text(), event.user_id, event.group_id)
    try:
        await bot.call_api("send_group_msg", group_id=event.group_id, message=message)
    except MatcherException:
        raise
    except Exception:
        logger.exception(f"发送进群欢迎失败：group={event.group_id} user={event.user_id}")
