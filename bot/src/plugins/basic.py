"""基础指令：/help /ping /id /status /状态（白名单群或超管私聊）。"""

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
from nonebot.plugin import get_loaded_plugins
from nonebot.rule import Rule
from nonebot.typing import T_State

from src.config import get_config, superuser_ids
from src.permission import BASIC, SUPERUSER, merged_whitelist


async def _to_me(event: MessageEvent) -> bool:
    """要求消息是对机器人说的（@、昵称前缀或私聊）。

    注意：NICKNAME 含「星潮」时，适配器会把「星潮帮助」剥成「帮助」并置 to_me=True，
    因此别名 matcher 匹配剥离后的文本，避免裸发「帮助」在群里误触发。
    """
    return event.to_me


COMMON_TEXT = (
    "✨ 我是星潮，一个开源QQbot!\n"
    "\n"
    "「📖 基础指令」\n"
    "  ◈ /ping — 连通测试\n"
    "  ◈ /id — 查看群号 / 用户 / 机器人 ID\n"
    "  ◈ /stats — 群活跃统计（/stats yesterday 看昨日）\n"
    "  ◈ /trending [daily|weekly] [语言] — GitHub 趋势榜\n"
    "  ◈ 关于星潮 — 机器人与开发者信息\n"
)

THIRD_PARTY_TEXT = (
    "\n「🎮 趣味互动」\n"
    "  ◈ 签到 / 积分 / 排行榜 / 挖矿 / 钓鱼 / 抽奖 — 云签到小游戏\n"
    "  ◈ 转账 / 打劫 @某人 / 出售 物品 — 积分玩法\n"
    "  ◈ 疯狂星期四 — KFC 疯四文案（也可 疯狂星期一~日）\n"
    "  ◈ /舔狗日记 / 讲个笑话 / 一言 — 文案与语录\n"
    "  ◈ /人生重开 — 人生重开模拟器\n"
    "  ◈ /轮盘 + /开枪 — 俄罗斯轮盘\n"
    "  ◈ /QR <内容> — 生成二维码（/QR帮助 看进阶玩法）\n"
    "  ◈ 戳一戳机器人 — 随机回复\n"
    "\n「🛠 实用功能」\n"
    "  ◈ /天气 <城市> — 实时天气\n"
    "  ◈ /发言统计 / 月发言统计 — 群活跃水群榜\n"
    "  ◈ /查询资料 <QQ> — QQ 资料卡片\n"
    "  ◈ 直接发 GitHub 链接 — 自动生成仓库卡片\n"
    "  ◈ 给消息贴 QQ 表情 — 机器人也会同款回应～\n"
)

ADMIN_TEXT = (
    "\n「🔑 管理指令 · 仅超管」\n"
    "  ◈ /status — 运行状态（插件/白名单/词库）\n"
    "  ◈ /状态 — 服务器资源（CPU/内存/磁盘，第三方插件）\n"
    "  ◈ /mute @某人 [分钟] — 禁言\n"
    "  ◈ /unmute @某人 — 解除禁言\n"
    "  ◈ /banall on|off — 全体禁言\n"
    "  ◈ /kick @某人 — 移出本群\n"
    "  ◈ /recall — 撤回（回复目标消息）\n"
    "  ◈ /reply reload|list — 关键词词库\n"
    "  ◈ /group list|add|del — 白名单管理\n"
    "  ◈ /superuser list|add|del — 超管管理\n"
    "  ◈ /ai on|off|status|clear — AI 问答管理\n"
    "  ◈ /通过|拒绝 <序号> — 入群申请审批\n"
    "  ◈ /pending — 待审批列表\n"
    "  ◈ /welcome on|off|set — 欢迎语配置\n"
    "  ◈ /task list — 定时任务列表\n"
    "  ◈ /notice <内容> — 发布群公告\n"
    "  ◈ /plugin reply|welcome on|off — 模块开关\n"
    "  ◈ /delete @某人 <条数> — 批量撤回（群管可用）\n"
    "  ◈ /拉黑用户|拉黑群 <目标> — 黑名单（详见文档）\n"
)

PUBLIC_TEXT = COMMON_TEXT + THIRD_PARTY_TEXT + (
    "\n✧ ─────────── ✧\n"
    "💌 途中遇到问题？联系超管处理吧～"
)

HELP_TEXT = PUBLIC_TEXT + ADMIN_TEXT

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


@help_cmd.handle()
@help_alias.handle()
async def handle_help(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    # 群主/管理员/超管看全部菜单；普通成员看公共菜单（趣味互动/实用功能也可见）
    if await _is_group_admin(bot, event):
        await _send(matcher, HELP_TEXT)
    else:
        await _send(matcher, PUBLIC_TEXT.replace(
            "💌 途中遇到问题？联系超管处理吧～",
            "💌 管理指令仅群管理与超管可见～",
        ))


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
