"""AI 工具：本群本地积分（签到 / 查询 / 排行 / 流水 / 补助 / 超管调分）。

操作的是 SQLite 里的按群本地积分，和 /商店、/轮盘 共用。
"""

from __future__ import annotations

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


@tool(
    "get_points",
    "查看提问者在当前群的本地积分余额、今日签到状态、连续签到天数、轮盘战绩"
    "（用户说“我有多少积分/今天签到了吗/我的积分/我多少分”时使用；只读）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_get_points(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：积分按群隔离，请在群里查询。"
    return _plain(await points.points_message(gid, event.user_id))


@tool(
    "sign_in",
    "在本群每日签到领积分（连续签到有加成；用户说“签到/帮我签到/打卡”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_sign_in(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：签到只能在群里进行。"
    _, message = await points.sign_in(gid, event.user_id)
    return _plain(message)


@tool(
    "claim_points_relief",
    "领取本群积分每日破产补助（余额低于阈值时才能领；用户说“领个补助/我没积分了/领救济”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_claim_points_relief(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：补助只能在群里领。"
    _, message = await points.relief_message(gid, event.user_id)
    return _plain(message)


@tool(
    "transfer_points",
    "在本群把提问者的积分转给另一个群友，按配置收手续费（手续费销毁；用户说“转给某人 50 积分/送他点分”时使用；不能转给自己）",
    {
        "type": "object",
        "properties": {
            "target_qq": {"type": "integer", "description": "收款人的 QQ 号"},
            "amount": {"type": "integer", "description": "转账数量，正整数"},
            "group_id": {"type": "integer"},
        },
        "required": ["target_qq", "amount"],
    },
    "all",
)
async def _t_transfer_points(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：积分按群隔离，只能在群里转账。"
    ok, text = await points.transfer(
        gid,
        event.user_id,
        int(args.get("target_qq") or 0),
        int(args.get("amount") or 0),
    )
    return text


@tool(
    "get_points_leaderboard",
    "查看本群本地积分排行榜（用户说“积分排行/谁分最多/排行榜”时使用；只读）",
    {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer"},
            "limit": {"type": "integer", "description": "显示前几名，默认 10"},
        },
        "required": [],
    },
    "all",
)
async def _t_points_leaderboard(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：积分按群隔离，请在群里查看。"
    return _plain(await points.leaderboard_message(gid, int(args.get("limit") or 10)))


@tool(
    "get_points_history",
    "查看提问者在本群的积分流水（用户说“我的积分记录/积分怎么来的/刚才扣了什么”时使用；只读）",
    {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer"},
            "limit": {"type": "integer", "description": "显示最近几条，默认 10"},
        },
        "required": [],
    },
    "all",
)
async def _t_points_history(bot, event, args) -> str:
    from src.plugins import points

    gid = _gid(event, args)
    if not gid:
        return "错误：积分按群隔离，请在群里查看。"
    return _plain(await points.history_message(gid, event.user_id, int(args.get("limit") or 10)))


@tool(
    "get_points_rules",
    "查看本群积分规则（签到奖励、连续加成、破产补助、轮盘结算；用户问“积分怎么玩/签到加多少”时使用；只读）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_points_rules(bot, event, args) -> str:
    from src.plugins import points

    return _plain(await points.rules_message())


@tool(
    "adjust_points",
    "超管手动调整某人在本群的积分，必须说明原因（用户说“给某人加/扣积分/补分”时使用；会写一条 admin 流水）",
    {
        "type": "object",
        "properties": {
            "target_qq": {"type": "integer", "description": "被调整人的 QQ 号"},
            "delta": {"type": "integer", "description": "正数加分，负数扣分"},
            "reason": {"type": "string", "description": "调整原因，会写入流水"},
            "group_id": {"type": "integer"},
        },
        "required": ["target_qq", "delta", "reason"],
    },
    "superuser",
)
async def _t_adjust_points(bot, event, args) -> str:
    from src.plugins import points

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：积分按群隔离，请在群里调整。"
    ok, text = await points.adjust_points(
        gid,
        event.user_id,
        int(args.get("target_qq") or 0),
        int(args.get("delta") or 0),
        str(args.get("reason") or ""),
    )
    return text


@tool(
    "set_points_config",
    "修改积分默认配置（仅超管；配置对所有群生效；key 可用首次开户/initial、每日补助/relief、"
    "补助阈值/relief_threshold、签到基础/sign_base、连续加成/sign_bonus、加成上限天数/sign_bonus_days）",
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
async def _t_set_points_config(bot, event, args) -> str:
    from src.plugins import points

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    _, text = await points.update_setting(str(args.get("key", "")), str(args.get("value", "")))
    return text
