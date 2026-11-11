"""基础指令：/帮助 /连通测试 /身份 /运行状态（白名单群或超管私聊；英文写法 /help /ping /id /status 仍可用）。"""

from __future__ import annotations

from nonebot import on_command, on_fullmatch
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import get_loaded_plugins
from nonebot.rule import Rule
from nonebot.typing import T_State

from src import helpmenu
from src.config import get_config, superuser_ids
from src.permission import BASIC, SUPERUSER, merged_whitelist


async def _to_me(event: MessageEvent) -> bool:
    """要求消息是对机器人说的（@、昵称前缀或私聊）。

    注意：NICKNAME 含「星潮」时，适配器会把「星潮帮助」剥成「帮助」并置 to_me=True，
    因此别名 matcher 匹配剥离后的文本，避免裸发「帮助」在群里误触发。
    """
    return event.to_me


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


# /help 以及别名「星潮帮助」「星潮 help」（昵称剥离后为「帮助」/「help」，需 to_me）
help_cmd = on_command("帮助", aliases={"help"}, rule=BASIC, priority=5, block=True)
help_alias = on_fullmatch({"帮助", "help"}, rule=BASIC & Rule(_to_me), priority=5, block=True)
ping_cmd = on_command("连通测试", aliases={"ping"}, rule=BASIC, priority=5, block=True)
id_cmd = on_command("身份", aliases={"id"}, rule=BASIC, priority=5, block=True)
status_cmd = on_command("运行状态", aliases={"status"}, rule=SUPERUSER, priority=5, block=True)


async def _is_group_admin(bot: Bot, event: MessageEvent) -> bool:
    """超管，或群内的群主/管理员。"""
    if event.user_id in superuser_ids():
        return True
    if not isinstance(event, GroupMessageEvent):
        return False
    try:
        m = await bot.call_api(
            "get_group_member_info",
            group_id=event.group_id, user_id=event.user_id, no_cache=True,
        )
        return m.get("role") in ("owner", "admin")
    except Exception:
        return False


async def send_help(bot: Bot, event: MessageEvent, matcher: Matcher, arg: str = "") -> None:
    """群主/管理员/超管看全部分类；普通成员看公共分类。「全部」在群里用合并转发发出。"""
    is_admin = await _is_group_admin(bot, event)
    text, want_all = helpmenu.help_text(arg, is_admin)
    if want_all:
        try:
            nodes = helpmenu.forward_nodes(is_admin, str(bot.self_id))
            if isinstance(event, GroupMessageEvent):
                await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
            else:
                await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
            return
        except Exception:
            logger.warning("帮助菜单合并转发失败，退回文字", exc_info=True)
    await _send(matcher, text)


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    await send_help(bot, event, matcher, args.extract_plain_text())


@help_alias.handle()
async def handle_help_alias(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    await send_help(bot, event, matcher)


about_cmd = on_command("关于星潮", rule=BASIC, priority=5, block=True)
about_alias = on_fullmatch({"关于星潮"}, rule=BASIC & Rule(_to_me), priority=5, block=True)


@about_cmd.handle()
@about_alias.handle()
async def handle_about(bot: Bot, matcher: Matcher) -> None:
    dev_id = get_config().xingchao_developer_id
    message = Message(
        "✨ 关于星潮\n"
        "\n"
        "开发者：XuYi（"
    ) + MessageSegment.at(dev_id) + Message(
        f"，QQ {dev_id}）\n"
        f"开发者博客：{get_config().xingchao_developer_blog}\n"
        "机器人官网：https://xingchao.dev\n"
        "项目开源地址：https://github.com/08820048/xingchao"
    )
    await _send(matcher, message)


@ping_cmd.handle()
async def handle_ping(matcher: Matcher) -> None:
    await _send(matcher, "pong")


@id_cmd.handle()
async def handle_id(event: MessageEvent, bot: Bot, matcher: Matcher) -> None:
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    lines = [f"group_id: {group_id if group_id is not None else '（私聊）'}",
             f"user_id: {event.user_id}",
             f"self_id: {bot.self_id}"]
    await _send(matcher, "\n".join(lines))


@status_cmd.handle()
async def handle_status(state: T_State, matcher: Matcher) -> None:
    cfg = get_config()
    from src.plugins import reply as reply_plugin

    plugin_names = sorted(
        p.name
        for p in get_loaded_plugins()
        if p.module is not None and p.module.__name__.startswith("src.plugins")
    )
    items = reply_plugin.get_items()
    enabled = sum(1 for it in items if it.get("enabled", True))
    lines = [
        "星潮 Xingchao 运行状态",
        f"在线: 是",
        f"插件: {', '.join(plugin_names) or '无'}",
        f"白名单群: {len(merged_whitelist())} 个",
        f"关键词词条: {len(items)} 条（启用 {enabled}，模块 {'开' if reply_plugin.is_enabled() else '关'}）",
    ]
    await _send(matcher, "\n".join(lines))
