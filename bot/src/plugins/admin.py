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
from src.permission import SUPERUSER, add_runtime_group, merged_whitelist, remove_runtime_group
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
    parts = args.extract_plain_text().strip().split()
    if not parts:
        await _send(group_admin, "用法：/group list | /group add <群号> | /group del <群号>")
        return
    action = parts[0].strip().lower()

    if action == "list":
        whitelist = merged_whitelist()
        if not whitelist:
            await _send(group_admin, "白名单为空（不处理任何群）。")
            return
        env_groups = get_config().xingchao_group_whitelist
        lines = [f"白名单 {len(whitelist)} 个群："]
        for g in sorted(whitelist):
            source = "env" if g in env_groups else "运行时"
            lines.append(f"  {g}（{source}）")
        await _send(group_admin, "\n".join(lines))
        return

    if action in ("add", "del") and len(parts) == 2 and parts[1].isdigit():
        group_id = int(parts[1])
        env_groups = get_config().xingchao_group_whitelist
        if action == "add":
            if await add_runtime_group(group_id):
                await _send(group_admin, f"已将群 {group_id} 加入白名单（立即生效，重启后保留）。")
            else:
                await _send(group_admin, f"群 {group_id} 已在白名单中。")
        else:
            if group_id in env_groups:
                await _send(
                    group_admin,
                    f"群 {group_id} 来自环境变量，无法运行时移除；请修改 XINGCHAO_GROUP_WHITELIST 后重启。",
                )
            elif await remove_runtime_group(group_id):
                await _send(group_admin, f"已将群 {group_id} 移出白名单（立即生效）。")
            else:
                await _send(group_admin, f"群 {group_id} 不在运行时白名单中。")
        return

    await _send(group_admin, "用法：/group list | /group add <群号> | /group del <群号>")


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
