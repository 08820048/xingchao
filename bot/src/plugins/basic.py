"""基础指令：/help /ping /id /status（白名单群或超管私聊）。"""

from __future__ import annotations

from nonebot import on_command, on_fullmatch
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.plugin import get_loaded_plugins
from nonebot.rule import Rule
from nonebot.typing import T_State

from src.config import get_config
from src.permission import BASIC, SUPERUSER


async def _to_me(event: MessageEvent) -> bool:
    """要求消息是对机器人说的（@、昵称前缀或私聊）。

    注意：NICKNAME 含「星潮」时，适配器会把「星潮帮助」剥成「帮助」并置 to_me=True，
    因此别名 matcher 匹配剥离后的文本，避免裸发「帮助」在群里误触发。
    """
    return event.to_me


HELP_TEXT = (
    "星潮 Xingchao\n"
    "https://xingchao.dev\n"
    "\n"
    "/ping - 连通测试\n"
    "/id - 查看群号 / 用户 / 机器人 ID\n"
    "/status - 运行状态（仅超管）\n"
    "/reply reload|list - 关键词词库管理（仅超管）\n"
    "/group list - 查看群白名单（仅超管）\n"
    "/plugin reply on|off - 关键词模块开关（仅超管）"
)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


# /help 以及别名「星潮帮助」「星潮 help」（昵称剥离后为「帮助」/「help」，需 to_me）
help_cmd = on_command("help", rule=BASIC, priority=5, block=True)
help_alias = on_fullmatch({"帮助", "help"}, rule=BASIC & Rule(_to_me), priority=5, block=True)
ping_cmd = on_command("ping", rule=BASIC, priority=5, block=True)
id_cmd = on_command("id", rule=BASIC, priority=5, block=True)
status_cmd = on_command("status", rule=SUPERUSER, priority=5, block=True)


@help_cmd.handle()
@help_alias.handle()
async def handle_help(matcher: Matcher) -> None:
    await _send(matcher, HELP_TEXT)


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
        f"白名单群: {len(cfg.xingchao_group_whitelist)} 个",
        f"关键词词条: {len(items)} 条（启用 {enabled}，模块 {'开' if reply_plugin.is_enabled() else '关'}）",
    ]
    await _send(matcher, "\n".join(lines))
