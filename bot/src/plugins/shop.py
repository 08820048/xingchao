"""按群积分商店：/商店、/购买、/兑换记录。

- 商品按群隔离；购买扣积分，兑换单先进入 pending，超管用 /商店 处理 <编号> 完成|拒绝 收尾
- 交付方式：
  - manual：人工处理（默认，最稳）
  - title：自动设置群专属头衔（需要机器人是群主，失败转人工）
  - announcement：自动发群公告（需要机器人是管理员，失败转人工）
- 拒绝兑换会退款并恢复库存；积分流水 reason=shop / shop_refund
- AI 工具：list_shop_items / buy_shop_item / list_my_redemptions /
  list_pending_redemptions / resolve_shop_redemption / add_shop_item / update_shop_item
"""

from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src import notify
from src.config import superuser_ids
from src.permission import GROUP_WHITELIST
from src.store import get_store

STATUS_LABEL = {"pending": "待处理", "fulfilled": "已完成", "rejected": "已拒绝"}
DELIVERY_LABEL = {"manual": "人工交付", "title": "自动头衔", "announcement": "自动公告"}

_locks: dict[int, asyncio.Lock] = {}


def _lock(group_id: int) -> asyncio.Lock:
    return _locks.setdefault(group_id, asyncio.Lock())


def _text(text: str) -> Message:
    return Message([MessageSegment.text(text)])


def _at(user_id: int) -> MessageSegment:
    return MessageSegment.at(int(user_id))


async def _notify_admins(bot: Bot | None, text: str) -> None:
    if bot is None:
        return
    try:
        await notify.notify_targets(bot, notify.superuser_ids(), text, kind="积分商店通知")
    except Exception:
        logger.exception("积分商店通知超管失败")


def _item_line(item: dict, index: int | None = None) -> str:
    stock = "不限量" if item["stock"] == -1 else f"库存 {item['stock']}"
    limit = "不限购" if not item["per_user_limit"] else f"每人限 {item['per_user_limit']} 次"
    delivery = DELIVERY_LABEL.get(item["delivery"], item["delivery"])
    prefix = f"{index}. " if index is not None else f"#{item['id']} "
    state = "" if item["enabled"] else " · 已下架"
    line = f"{prefix}{item['name']} — {item['price']} 积分（{stock} · {limit} · {delivery}{state}）"
    if item["description"]:
        line += f"\n   {item['description']}"
    return line


async def list_message(group_id: int, include_disabled: bool = False) -> Message:
    items = await get_store().shop_items(group_id, include_disabled=include_disabled)
    if not items:
        return _text(
            "🛒 本群商店还没有商品。\n"
            "超管可用 /商店 添加 <名称> <价格> [描述] 上架，或 /商店 设置 调整库存/限购/交付方式。"
        )
    lines = ["🛒 本群积分商店"]
    for item in items:
        lines.append(_item_line(item, item["id"]))
    lines.append("购买：/购买 <编号> [备注] · 我的记录：/兑换记录")
    return _text("\n".join(lines))


async def buy(
    bot: Bot, group_id: int, user_id: int, item_id: int, user_note: str = ""
) -> tuple[bool, Message]:
    """购买并尝试自动交付；失败/人工商品会通知超管。"""
    from src.plugins import points

    async with _lock(group_id):
        settings = await points.load_settings()
        store = get_store()
        await store.ensure_points(group_id, user_id, settings["initial"])
        ok, redemption_id, text = await store.buy_shop_item(
            group_id, user_id, item_id, user_note
        )
        if not ok:
            return False, _text(text)
        item = await store.shop_item(group_id, item_id)
        if item is None:  # 理论不会发生：刚买到的商品被删了
            return True, _text(f"{text}\n兑换单 #{redemption_id} 等待管理员处理。")
        delivered = False
        delivery_note = ""
        if item["delivery"] == "title":
            from src.plugins import groupextras

            title = item["delivery_data"] or user_note or item["name"]
            delivered, delivery_note = await groupextras.set_title(
                bot, group_id, user_id, title
            )
        elif item["delivery"] == "announcement":
            content = item["delivery_data"] or user_note or item["name"]
            try:
                await bot.call_api("_send_group_notice", group_id=group_id, content=content)
                delivered = True
            except Exception:
                try:
                    await bot.call_api("send_group_notice", group_id=group_id, content=content)
                    delivered = True
                except Exception as e:
                    delivery_note = f"发送群公告失败：{str(getattr(e, 'info', None) or e)[:60]}"
        if delivered:
            await store.resolve_shop_redemption(
                group_id, redemption_id, "自动交付", True, delivery_note or "自动交付成功"
            )
            return True, _text(
                f"{text}\n兑换单 #{redemption_id} 已自动交付。"
                + (f"\n{delivery_note}" if delivery_note else "")
            )
        # 人工/自动失败：留 pending，通知超管
        reason = delivery_note or (
            "人工交付商品" if item["delivery"] == "manual" else "自动交付失败"
        )
        await _notify_admins(
            bot,
            f"🛒 新兑换待处理\n群 {group_id} · 用户 {user_id}\n"
            f"兑换单 #{redemption_id} · {item['name']} · {item['price']} 积分\n"
            f"备注：{user_note or '无'}\n{reason}\n"
            f"处理：/商店 处理 {redemption_id} 完成|拒绝 [备注]",
        )
        return True, _text(
            f"{text}\n兑换单 #{redemption_id} 已进入待处理，超管会尽快处理。"
            + (f"\n{delivery_note}" if delivery_note else "")
        )


async def resolve(
    bot: Bot, group_id: int, resolver: int | str, redemption_id: int, accept: bool, note: str = ""
) -> tuple[bool, Message]:
    async with _lock(group_id):
        ok, text = await get_store().resolve_shop_redemption(
            group_id, redemption_id, str(resolver), accept, note
        )
        if ok:
            await _notify_admins(bot, f"🛒 兑换单 #{redemption_id} 已由 {resolver} 处理：{text}")
        return ok, _text(text)


async def pending_message(group_id: int) -> Message:
    rows = await get_store().shop_redemptions(group_id, status="pending", limit=20)
    if not rows:
        return _text("本群没有待处理的兑换单。")
    lines = ["🛒 待处理兑换单"]
    for row in rows:
        lines.append(
            f"#{row['id']} · {row['user_id']} · {row['item_name']} · {row['price']} 积分"
            + (f"\n   备注：{row['user_note']}" if row["user_note"] else "")
            + f"\n   /商店 处理 {row['id']} 完成|拒绝 [备注]"
        )
    return _text("\n".join(lines))


async def stats_message(group_id: int, days: int = 30) -> Message:
    stats = await get_store().shop_stats(group_id, days)
    if not stats["orders"]:
        return _text(f"🛒 最近 {stats['days']} 天还没有兑换单。")
    lines = [
        f"🛒 商店统计（最近 {stats['days']} 天）",
        f"兑换单 {stats['orders']} 张：待处理 {stats['pending']} · 已完成 {stats['fulfilled']} · 已拒绝 {stats['rejected']}",
        f"买家 {stats['buyers']} 人 · 净消耗 {stats['gross']} 积分 · 已退款 {stats['refunded']} 积分",
    ]
    if stats["top"]:
        lines.append("热门商品：")
        for index, item in enumerate(stats["top"], 1):
            lines.append(f"  {index}. {item['item_name']} ×{item['count']} · {item['points']} 积分")
    return _text("\n".join(lines))


async def records_message(group_id: int, user_id: int) -> Message:
    rows = await get_store().shop_redemptions(group_id, user_id=user_id, limit=20)
    if not rows:
        return _text("你还没有兑换记录。发 /商店 看看有什么。")
    lines = ["🛒 你的兑换记录"]
    for row in rows:
        when = str(row["created_at"]).replace("T", " ")[5:16]
        line = f"#{row['id']} {row['item_name']} · {row['price']} 积分 · {STATUS_LABEL.get(row['status'], row['status'])} · {when}"
        if row["resolve_note"]:
            line += f"\n   处理备注：{row['resolve_note']}"
        lines.append(line)
    return _text("\n".join(lines))


async def add_item(group_id: int, name: str, price: int, description: str = "") -> tuple[bool, str]:
    return await get_store().add_shop_item(
        group_id, name, price, description=description
    )


async def update_item(group_id: int, item_id: int, field: str, value: str) -> tuple[bool, str]:
    store = get_store()
    field = field.strip().lower()
    mapping = {
        "名称": {"name": value}, "name": {"name": value},
        "描述": {"description": value}, "description": {"description": value},
        "价格": {"price": value}, "price": {"price": value},
        "库存": {"stock": value}, "stock": {"stock": value},
        "限购": {"per_user_limit": value}, "per_user_limit": {"per_user_limit": value},
        "交付": {"delivery": value}, "delivery": {"delivery": value},
        "交付内容": {"delivery_data": value}, "delivery_data": {"delivery_data": value},
        "上架": {"enabled": True}, "开": {"enabled": True}, "on": {"enabled": True},
        "下架": {"enabled": False}, "关": {"enabled": False}, "off": {"enabled": False},
        "删除": {"enabled": False}, "del": {"enabled": False},
    }
    if field not in mapping:
        return False, "可设置：名称/描述/价格/库存/限购/交付/交付内容/上架/下架"
    return await store.update_shop_item(group_id, item_id, mapping[field])


# ---------------------------------------------------------------- 指令

shop_cmd = on_command("商店", aliases={"shop"}, rule=GROUP_WHITELIST, priority=5, block=True)
buy_cmd = on_command(
    "购买", aliases={"buy", "兑换"}, rule=GROUP_WHITELIST, priority=5, block=True
)
orders_cmd = on_command(
    "兑换记录", aliases={"orders"}, rule=GROUP_WHITELIST, priority=5, block=True
)


@shop_cmd.handle()
async def handle_shop(
    matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    raw = args.extract_plain_text().strip()
    parts = raw.split()
    head = parts[0] if parts else ""
    if not raw or head in ("列表", "list", "全部"):
        include_disabled = head == "全部" and event.user_id in superuser_ids()
        await matcher.send(await list_message(event.group_id, include_disabled))
        return
    if head in ("规则", "help", "说明"):
        await matcher.send(
            _text(
                "🛒 积分商店\n"
                "· /商店 看商品，/购买 <编号> [备注] 购买，/兑换记录 看自己的兑换单。\n"
                "· 购买立即扣积分；兑换单先进入待处理，超管 /商店 处理 <编号> 完成|拒绝。\n"
                "· 拒绝会退款并恢复库存；自动头衔/公告失败也会转人工。\n"
                "· 超管：/商店 添加 <名称> <价格> [描述]、/商店 设置 <编号> <字段> <值>、"
                "/商店 待处理。"
            )
        )
        return
    if event.user_id not in superuser_ids():
        await matcher.finish("商店管理只有超管能用。")
    if head in ("添加", "add"):
        if len(parts) < 3:
            await matcher.finish("用法：/商店 添加 <名称> <价格> [描述]（名称不要带空格）")
        try:
            price = int(parts[2])
        except ValueError:
            await matcher.finish("价格必须是整数")
        _, text = await add_item(event.group_id, parts[1], price, " ".join(parts[3:]))
        await matcher.send(text)
        return
    if head in ("设置", "set"):
        if len(parts) < 3:
            await matcher.finish(
                "用法：/商店 设置 <编号> <名称|描述|价格|库存|限购|交付|交付内容|上架|下架> [值]"
            )
        try:
            item_id = int(parts[1])
        except ValueError:
            await matcher.finish("编号必须是整数")
        if len(parts) < 4 and parts[2] not in ("上架", "下架", "开", "关", "on", "off", "删除", "del"):
            await matcher.finish("这个字段需要一个值")
        _, text = await update_item(
            event.group_id, item_id, parts[2], " ".join(parts[3:])
        )
        await matcher.send(text)
        return
    if head in ("删除", "下架", "del"):
        if len(parts) < 2:
            await matcher.finish("用法：/商店 删除 <编号>")
        try:
            item_id = int(parts[1])
        except ValueError:
            await matcher.finish("编号必须是整数")
        _, text = await update_item(event.group_id, item_id, "下架", "")
        await matcher.send(text)
        return
    if head in ("待处理", "pending"):
        await matcher.send(await pending_message(event.group_id))
        return
    if head in ("统计", "stats"):
        days = 30
        if len(parts) > 1:
            try:
                days = int(parts[1])
            except ValueError:
                await matcher.finish("天数必须是整数")
        await matcher.send(await stats_message(event.group_id, days))
        return
    if head in ("处理", "resolve"):
        if len(parts) < 3:
            await matcher.finish("用法：/商店 处理 <编号> 完成|拒绝 [备注]")
        try:
            redemption_id = int(parts[1])
        except ValueError:
            await matcher.finish("兑换单编号必须是整数")
        accept_word = parts[2].lower()
        if accept_word in ("完成", "通过", "同意", "accept", "ok", "yes"):
            accept = True
        elif accept_word in ("拒绝", "驳回", "reject", "no"):
            accept = False
        else:
            await matcher.finish("第二个参数应为 完成 或 拒绝")
        _, msg = await resolve(
            bot, event.group_id, event.user_id, redemption_id, accept, " ".join(parts[3:])
        )
        await matcher.send(msg)
        return
    await matcher.finish("用法：/商店 | /商店 添加|设置|待处理|处理|统计")


@buy_cmd.handle()
async def handle_buy(
    matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    parts = args.extract_plain_text().strip().split()
    if not parts:
        await matcher.finish("用法：/购买 <编号> [备注]")
    try:
        item_id = int(parts[0])
    except ValueError:
        await matcher.finish("商品编号必须是整数")
    ok, msg = await buy(bot, event.group_id, event.user_id, item_id, " ".join(parts[1:]))
    await matcher.send(msg)


@orders_cmd.handle()
async def handle_orders(matcher: Matcher, event: GroupMessageEvent) -> None:
    await matcher.send(await records_message(event.group_id, event.user_id))
