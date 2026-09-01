"""超管指令：/reply reload|list、/group list、/plugin reply on|off。

仅 SUPERUSERS，群内或私聊可用。运行时改白名单不在第一期范围（改 env 后重启）。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import SUPERUSER
from src.store import get_store

reply_admin = on_command("reply", rule=SUPERUSER, priority=1, block=True)
group_admin = on_command("group", rule=SUPERUSER, priority=1, block=True)
plugin_admin = on_command("plugin", rule=SUPERUSER, priority=1, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


@reply_admin.handle()
async def handle_reply(args: Message = CommandArg()) -> None:
    from src.plugins import reply as reply_plugin

    action = args.extract_plain_text().strip().lower()
    if action == "reload":
        count = reply_plugin.reload_replies()
        await _send(reply_admin, f"词库已重载，共 {count} 条词条。")
    elif action == "list":
        items = reply_plugin.get_items()
        if not items:
            await _send(reply_admin, "词库为空。")
            return
        lines = [f"词条 {len(items)} 条："]
        for i, it in enumerate(items, 1):
            status = "启用" if it.get("enabled", True) else "停用"
            lines.append(f"{i}. {it['id']} [{it.get('match', 'exact')}] {it['pattern']} ({status})")
        await _send(reply_admin, "\n".join(lines))
    else:
        await _send(reply_admin, "用法：/reply reload 或 /reply list")


@group_admin.handle()
async def handle_group(args: Message = CommandArg()) -> None:
    action = args.extract_plain_text().strip().lower()
    if action != "list":
        await _send(group_admin, "用法：/group list")
        return
    whitelist = get_config().xingchao_group_whitelist
    if not whitelist:
        await _send(group_admin, "白名单为空（不处理任何群）。请修改 XINGCHAO_GROUP_WHITELIST 后重启。")
        return
    await _send(group_admin, f"白名单 {len(whitelist)} 个群：\n" + "\n".join(str(g) for g in sorted(whitelist)))


@plugin_admin.handle()
async def handle_plugin(args: Message = CommandArg()) -> None:
    from src.plugins import reply as reply_plugin

    parts = args.extract_plain_text().strip().lower().split()
    if len(parts) != 2 or parts[0] != "reply" or parts[1] not in ("on", "off"):
        await _send(plugin_admin, "用法：/plugin reply on 或 /plugin reply off")
        return
    enable = parts[1] == "on"
    reply_plugin.set_enabled(enable)
    try:
        await get_store().set_kv("reply_enabled", "true" if enable else "false")
    except Exception:
        logger.exception("写入 reply_enabled 开关失败（内存开关已生效，重启后恢复默认）")
    await _send(plugin_admin, f"关键词模块已{'开启' if enable else '关闭'}。")
