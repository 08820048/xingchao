"""AI 工具：本群积分商店（商品、购买、兑换记录、超管处理）。"""

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
    "list_shop_items",
    "查看本群积分商店的商品（用户说“商店有什么/积分能买什么/看看商品”时使用；只读）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_shop_items(bot, event, args) -> str:
    from src.plugins import shop

    gid = _gid(event, args)
    if not gid:
        return "错误：积分商店按群隔离，请在群里查看。"
    return _plain(await shop.list_message(gid))


@tool(
    "buy_shop_item",
    "用提问者的积分购买本群商店商品（用户说“买 3 号/兑换头衔/我要买这个”时使用；会立即扣积分并生成兑换单）",
    {
        "type": "object",
        "properties": {
            "item_id": {"type": "integer", "description": "商品编号，来自 list_shop_items"},
            "user_note": {"type": "string", "description": "给管理员的备注，可空"},
            "group_id": {"type": "integer"},
        },
        "required": ["item_id"],
    },
    "all",
)
async def _t_buy_shop_item(bot, event, args) -> str:
    from src.plugins import shop

    gid = _gid(event, args)
    if not gid:
        return "错误：积分商店按群隔离，请在群里购买。"
    _, message = await shop.buy(
        bot, gid, event.user_id, int(args.get("item_id") or 0), str(args.get("user_note") or "")
    )
    return _plain(message)


@tool(
    "list_my_redemptions",
    "查看提问者在本群的兑换记录（用户说“我买的东西到哪了/兑换记录/处理了吗”时使用；只读）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_my_redemptions(bot, event, args) -> str:
    from src.plugins import shop

    gid = _gid(event, args)
    if not gid:
        return "错误：兑换记录按群隔离，请在群里查看。"
    return _plain(await shop.records_message(gid, event.user_id))


@tool(
    "list_pending_redemptions",
    "查看本群待处理的商店兑换单（仅超管；用户说“有没有待处理的兑换/商店订单”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "superuser",
)
async def _t_pending_redemptions(bot, event, args) -> str:
    from src.plugins import shop

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：商店按群隔离，请在群里查看。"
    return _plain(await shop.pending_message(gid))


@tool(
    "get_shop_stats",
    "查看本群商店近 N 天的兑换统计与热门商品（仅超管；用户说“商店卖得怎么样/哪个商品最热/最近兑换多少”时使用；只读）",
    {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "统计天数，默认 30"},
            "group_id": {"type": "integer"},
        },
        "required": [],
    },
    "superuser",
)
async def _t_shop_stats(bot, event, args) -> str:
    from src.plugins import shop

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：商店按群隔离，请在群里查看。"
    return _plain(await shop.stats_message(gid, int(args.get("days") or 30)))


@tool(
    "resolve_shop_redemption",
    "完成或拒绝一张商店兑换单（仅超管；拒绝会自动退款并恢复库存；用户说“把 3 号兑换完成/拒绝 5 号”时使用）",
    {
        "type": "object",
        "properties": {
            "redemption_id": {"type": "integer"},
            "accept": {"type": "boolean", "description": "true=完成，false=拒绝并退款"},
            "note": {"type": "string", "description": "处理备注，可空"},
            "group_id": {"type": "integer"},
        },
        "required": ["redemption_id", "accept"],
    },
    "superuser",
)
async def _t_resolve_redemption(bot, event, args) -> str:
    from src.plugins import shop

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：商店按群隔离，请在群里处理。"
    _, message = await shop.resolve(
        bot, gid, event.user_id, int(args.get("redemption_id") or 0),
        bool(args.get("accept")), str(args.get("note") or ""),
    )
    return _plain(message)


@tool(
    "add_shop_item",
    "在本群上架一个积分商品（仅超管；默认人工交付，可指定自动头衔/公告）",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "price": {"type": "integer", "description": "价格，正整数"},
            "description": {"type": "string"},
            "stock": {"type": "integer", "description": "库存，-1 表示不限量"},
            "per_user_limit": {"type": "integer", "description": "每人限购，0 表示不限购"},
            "delivery": {"type": "string", "enum": ["manual", "title", "announcement"]},
            "delivery_data": {"type": "string", "description": "自动头衔文字或公告内容"},
            "group_id": {"type": "integer"},
        },
        "required": ["name", "price"],
    },
    "superuser",
)
async def _t_add_shop_item(bot, event, args) -> str:
    from src.store import get_store

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：商店按群隔离，请在群里上架。"
    _, _, text = await get_store().add_shop_item(
        gid,
        str(args.get("name") or ""),
        int(args.get("price") or 0),
        description=str(args.get("description") or ""),
        stock=int(args.get("stock") if args.get("stock") is not None else -1),
        per_user_limit=int(args.get("per_user_limit") or 0),
        delivery=str(args.get("delivery") or "manual"),
        delivery_data=str(args.get("delivery_data") or ""),
    )
    return text


@tool(
    "update_shop_item",
    "修改本群商品（仅超管；field 可用 名称/描述/价格/库存/限购/交付/交付内容/上架/下架）",
    {
        "type": "object",
        "properties": {
            "item_id": {"type": "integer"},
            "field": {"type": "string"},
            "value": {"type": "string"},
            "group_id": {"type": "integer"},
        },
        "required": ["item_id", "field", "value"],
    },
    "superuser",
)
async def _t_update_shop_item(bot, event, args) -> str:
    from src.plugins import shop

    if event.user_id not in superuser_ids():
        return "这个操作需要超管权限。"
    gid = _gid(event, args)
    if not gid:
        return "错误：商店按群隔离，请在群里修改。"
    _, text = await shop.update_item(
        gid, int(args.get("item_id") or 0),
        str(args.get("field") or ""), str(args.get("value") or ""),
    )
    return text
