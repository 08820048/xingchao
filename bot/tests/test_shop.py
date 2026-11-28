"""积分商店：商品、购买扣款/扣库存、限购、退款、自动交付。"""

from __future__ import annotations

import asyncio

import pytest

from src.plugins import shop
from src.store import Store


class FakeBot:
    self_id = "1"

    async def call_api(self, name, **kwargs):
        return {}


@pytest.fixture(autouse=True)
def _reset_shop():
    shop._locks.clear()
    yield
    shop._locks.clear()


# ---------------------------------------------------------------- 存储

async def test_add_list_update_item(store: Store):
    ok, item_id, _ = await store.add_shop_item(
        1, "自定义头衔", 100, "给自己一个群头衔", stock=2, per_user_limit=1
    )
    assert ok and item_id > 0
    [item] = await store.shop_items(1)
    assert item["name"] == "自定义头衔" and item["price"] == 100
    assert item["stock"] == 2 and item["per_user_limit"] == 1
    ok, text = await store.update_shop_item(
        1, item_id, {"name": "新头衔", "description": "新描述", "price": 80, "enabled": False}
    )
    assert ok and "已更新" in text
    assert await store.shop_items(1) == []
    [disabled] = await store.shop_items(1, include_disabled=True)
    assert disabled["name"] == "新头衔" and disabled["description"] == "新描述"
    assert disabled["price"] == 80 and disabled["enabled"] is False


async def test_buy_deducts_points_and_stock(store: Store):
    await store.ensure_points(1, 1, 200)
    ok, item_id, _ = await store.add_shop_item(1, "头衔", 50, stock=2)
    assert ok
    ok, redemption_id, text = await store.buy_shop_item(1, 1, item_id, "想要")
    assert ok and redemption_id > 0 and "扣 50" in text
    assert await store.get_points(1, 1) == 150
    assert (await store.shop_item(1, item_id))["stock"] == 1
    [row] = await store.shop_redemptions(1, user_id=1)
    assert row["status"] == "pending" and row["price"] == 50 and row["user_note"] == "想要"
    ledger = await store.points_history(1, 1)
    assert ledger[0]["reason"] == "shop" and ledger[0]["delta"] == -50


async def test_per_user_limit_and_stock(store: Store):
    await store.ensure_points(1, 1, 1000)
    await store.ensure_points(1, 2, 1000)
    ok, limited, _ = await store.add_shop_item(1, "限购", 10, per_user_limit=1)
    assert ok
    assert (await store.buy_shop_item(1, 1, limited))[0]
    ok, _, text = await store.buy_shop_item(1, 1, limited)
    assert not ok and "限购" in text

    ok, scarce, _ = await store.add_shop_item(1, "限量", 10, stock=1)
    assert ok
    assert (await store.buy_shop_item(1, 1, scarce))[0]
    ok, _, text = await store.buy_shop_item(1, 2, scarce)
    assert not ok and "库存不足" in text
    assert await store.get_points(1, 2) == 1000


async def test_reject_refunds_and_restores_stock(store: Store):
    await store.ensure_points(1, 1, 100)
    ok, item_id, _ = await store.add_shop_item(1, "退款商品", 30, stock=1)
    assert ok
    ok, redemption_id, _ = await store.buy_shop_item(1, 1, item_id)
    assert ok and await store.get_points(1, 1) == 70
    ok, text = await store.resolve_shop_redemption(1, redemption_id, "9", False, "没库存了")
    assert ok and "退款 30" in text
    assert await store.get_points(1, 1) == 100
    assert (await store.shop_item(1, item_id))["stock"] == 1
    [row] = await store.shop_redemptions(1, user_id=1)
    assert row["status"] == "rejected" and row["resolve_note"] == "没库存了"
    assert (await store.points_history(1, 1))[0]["reason"] == "shop_refund"


async def test_fulfill_does_not_refund(store: Store):
    await store.ensure_points(1, 1, 100)
    ok, item_id, _ = await store.add_shop_item(1, "人工商品", 40)
    assert ok
    ok, redemption_id, _ = await store.buy_shop_item(1, 1, item_id)
    assert ok
    ok, text = await store.resolve_shop_redemption(1, redemption_id, "9", True, "已处理")
    assert ok and "已完成" in text
    assert await store.get_points(1, 1) == 60
    assert (await store.shop_redemptions(1, user_id=1))[0]["status"] == "fulfilled"


async def test_buy_insufficient_leaves_no_redemption(store: Store):
    await store.ensure_points(1, 1, 10)
    ok, item_id, _ = await store.add_shop_item(1, "太贵", 50)
    assert ok
    ok, _, text = await store.buy_shop_item(1, 1, item_id)
    assert not ok and "积分不够" in text
    assert await store.get_points(1, 1) == 10
    assert await store.shop_redemptions(1, user_id=1) == []


async def test_concurrent_buy_never_oversells(store: Store):
    await store.ensure_points(1, 1, 100)
    await store.ensure_points(1, 2, 100)
    ok, item_id, _ = await store.add_shop_item(1, "最后一个", 10, stock=1)
    assert ok
    results = await asyncio.gather(
        store.buy_shop_item(1, 1, item_id),
        store.buy_shop_item(1, 2, item_id),
    )
    assert sum(1 for ok, *_ in results if ok) == 1
    assert (await store.shop_item(1, item_id))["stock"] == 0


# ---------------------------------------------------------------- 插件

async def test_buy_manual_creates_pending_and_notifies(store: Store, monkeypatch):
    monkeypatch.setattr(shop, "get_store", lambda: store)
    notified = []

    async def fake_notify(bot, text):
        notified.append(text)

    monkeypatch.setattr(shop, "_notify_admins", fake_notify)
    from src.plugins import points

    monkeypatch.setattr(points, "get_store", lambda: store)
    await store.ensure_points(1, 1, 100)
    _, item_id, _ = await store.add_shop_item(1, "人工商品", 40)
    ok, message = await shop.buy(FakeBot(), 1, 1, item_id, "备注")
    assert ok and "待处理" in message.extract_plain_text()
    assert notified and "人工商品" in notified[0]
    [row] = await store.shop_redemptions(1, user_id=1)
    assert row["status"] == "pending"


async def test_auto_title_delivers_and_fails_to_pending(store: Store, monkeypatch):
    monkeypatch.setattr(shop, "get_store", lambda: store)

    async def fake_notify(bot, text):
        return None

    monkeypatch.setattr(shop, "_notify_admins", fake_notify)
    from src.plugins import groupextras, points

    monkeypatch.setattr(points, "get_store", lambda: store)
    await store.ensure_points(1, 1, 100)
    _, item_id, _ = await store.add_shop_item(
        1, "自动头衔", 20, delivery="title", delivery_data="摸鱼王"
    )

    async def ok_title(bot, group_id, user_id, title):
        return True, f"已设置 {title}"

    monkeypatch.setattr(groupextras, "set_title", ok_title)
    ok, message = await shop.buy(FakeBot(), 1, 1, item_id)
    assert ok and "自动交付" in message.extract_plain_text()
    assert (await store.shop_redemptions(1, user_id=1))[0]["status"] == "fulfilled"

    await store.ensure_points(1, 2, 100)
    _, item_id2, _ = await store.add_shop_item(
        1, "自动头衔2", 20, delivery="title", delivery_data="头衔"
    )

    async def fail_title(bot, group_id, user_id, title):
        return False, "需要机器人是群主"

    monkeypatch.setattr(groupextras, "set_title", fail_title)
    ok, message = await shop.buy(FakeBot(), 1, 2, item_id2)
    assert ok and "待处理" in message.extract_plain_text()
    assert "需要机器人是群主" in message.extract_plain_text()


async def test_shop_stats_counts_and_top_items(store: Store):
    await store.ensure_points(1, 1, 1000)
    await store.ensure_points(1, 2, 1000)
    _, item_a, _ = await store.add_shop_item(1, "热门头衔", 100)
    _, item_b, _ = await store.add_shop_item(1, "冷门商品", 50)
    ok, r1, _ = await store.buy_shop_item(1, 1, item_a)      # pending
    ok2, r2, _ = await store.buy_shop_item(1, 2, item_a)     # fulfill
    ok3, r3, _ = await store.buy_shop_item(1, 1, item_b)     # reject
    assert ok and ok2 and ok3
    assert (await store.resolve_shop_redemption(1, r2, "9", True))[0]
    assert (await store.resolve_shop_redemption(1, r3, "9", False))[0]

    stats = await store.shop_stats(1, 30)
    assert stats["orders"] == 3
    assert (stats["pending"], stats["fulfilled"], stats["rejected"]) == (1, 1, 1)
    assert stats["buyers"] == 2
    assert stats["gross"] == 200  # pending 100 + fulfilled 100；被拒绝的 50 不算
    assert stats["refunded"] == 50
    assert stats["top"][0]["item_name"] == "热门头衔"
    assert stats["top"][0]["count"] == 2 and stats["top"][0]["points"] == 200
    assert len(stats["daily"]) == 1


async def test_stats_message(store: Store, monkeypatch):
    monkeypatch.setattr(shop, "get_store", lambda: store)
    await store.ensure_points(1, 1, 1000)
    _, item_id, _ = await store.add_shop_item(1, "头衔", 100)
    assert (await store.buy_shop_item(1, 1, item_id))[0]
    text = (await shop.stats_message(1, 30)).extract_plain_text()
    assert "兑换单 1 张" in text and "热门商品" in text and "头衔" in text
