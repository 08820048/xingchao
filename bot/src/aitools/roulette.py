"""AI 工具：轮盘积分局（走本群本地积分）。

多人回合制游戏，AI 只代替「发起人/当前开枪者」执行其本人能做的动作；
轮到别人时工具会返回提示，不会替别人开枪。查询类工具只读。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot

from src.aitools.registry import tool
from src.config import superuser_ids


def _gid(event, args) -> int:
    value = args.get("group_id") or getattr(event, "group_id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _plain(message) -> str:
    return message.extract_plain_text() if hasattr(message, "extract_plain_text") else str(message)


async def _deliver(bot: Bot, group_id: int, ok: bool, message) -> str:
    text = _plain(message)
    if not ok:
        return text
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=message)
    except Exception as e:
        return f"动作已执行，但发送到群里失败：{e}。结果：{text[:200]}"
    return f"已发到群里：{text[:300]}"


@tool(
    "get_roulette_status",
    "查看本群轮盘局状态和提问者的本地积分（用户说“轮盘现在什么情况/轮到谁了/我有多少积分”时使用；只读）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_roulette_status(bot, event, args) -> str:
    from src.plugins import points, roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里查看。"
    status = _plain(await roulette.status_message(gid))
    account = _plain(await points.points_message(gid, event.user_id))
    return f"{status}\n\n{account}"


@tool(
    "get_roulette_rules",
    "查看本群轮盘玩的规则、赔率与当前配置（用户问“轮盘怎么玩/赔率多少/中枪扣多少”时使用；只读）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_roulette_rules(bot, event, args) -> str:
    from src.plugins import roulette

    return _plain(await roulette.rules_message())


@tool(
    "start_roulette",
    "在本群开一局轮盘（提问者当庄家；之后群友发 /上车 或让 AI 帮忙加入，再 /开局）。用户说“开一局轮盘/来个轮盘局”时使用",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_start_roulette(bot, event, args) -> str:
    from src.plugins import roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里开。"
    ok, message = await roulette.start_lobby(bot, gid, event.user_id)
    return await _deliver(bot, gid, ok, message)


@tool(
    "begin_roulette",
    "开始本群已经有人在等的轮盘局（仅庄家或超管；用户说“开始吧/开局/别等了”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_begin_roulette(bot, event, args) -> str:
    from src.plugins import roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里开始。"
    ok, message = await roulette.begin_game(bot, gid, event.user_id)
    return await _deliver(bot, gid, ok, message)


@tool(
    "join_roulette",
    "加入本群正在等待的轮盘局（用户说“我也上车/算我一个/加入轮盘”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_join_roulette(bot, event, args) -> str:
    from src.plugins import roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里加入。"
    ok, message = await roulette.join_lobby(bot, gid, event.user_id)
    return await _deliver(bot, gid, ok, message)


@tool(
    "shoot_roulette",
    "轮到自己时开一枪（用户说“我开枪/开枪”时使用；不是提问者的回合会返回提示，不要替别人开枪）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_shoot_roulette(bot, event, args) -> str:
    from src.plugins import roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里开枪。"
    ok, message = await roulette.shoot(bot, gid, event.user_id)
    return await _deliver(bot, gid, ok, message)


@tool(
    "cancel_roulette",
    "取消本群还在等人的轮盘局（仅庄家或超管；用户说“不玩了/散了吧/取消轮盘”时使用；已开局时只有超管能强制中止）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_cancel_roulette(bot, event, args) -> str:
    from src.plugins import roulette

    gid = _gid(event, args)
    if not gid:
        return "错误：轮盘局只能在群里取消。"
    ok, message = await roulette.cancel_lobby(bot, gid, event.user_id)
    return await _deliver(bot, gid, ok, message)


@tool(
    "set_roulette_config",
    "修改轮盘玩法参数（仅超管；key 可用底注/stake、组织费/bonus、"
    "最少人数/min_players、最多人数/max_players、大厅等待秒数/lobby_seconds、"
    "回合超时秒数/turn_seconds、开局冷却/cooldown_seconds）",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "integer"},
        },
        "required": ["key", "value"],
    },
    "superuser",
)
async def _t_set_roulette_config(bot, event, args) -> str:
    from src.plugins import roulette

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    _, text = await roulette.update_setting(str(args.get("key", "")), str(args.get("value", "")))
    return text
