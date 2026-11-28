"""本地按群积分：账本、流水、幂等每日奖励、签到连续天数、迁移。"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, timedelta

from src.plugins import points
from src.store import Store


# ---------------------------------------------------------------- 存储

async def test_ensure_points_writes_initial_ledger(store: Store):
    assert await store.ensure_points(1, 1, 100) == 100
    assert await store.ensure_points(1, 1, 100) == 100  # 幂等，不重复送
    history = await store.points_history(1, 1)
    assert len(history) == 1
    assert history[0]["reason"] == "initial" and history[0]["delta"] == 100
    assert await store.add_points(1, 1, -20, "admin", note="测试扣分") == 80
    history = await store.points_history(1, 1)
    assert history[0]["delta"] == -20 and history[0]["balance_after"] == 80
    assert history[0]["note"] == "测试扣分"
    assert await store.top_points(1) == [(1, 80)]


async def test_add_points_batch_is_atomic_and_writes_ledger(store: Store):
    await store.ensure_points(1, 1, 100)
    await store.ensure_points(1, 2, 100)
    balances = await store.add_points_batch(
        1, {1: 10, 2: -10}, "transfer", note="互转"
    )
    assert balances == {1: 110, 2: 90}
    assert [r["balance_after"] for r in await store.points_history(1, 1)][:1] == [110]
    assert await store.get_points(1, 2) == 90


async def test_claim_daily_idempotent_and_threshold(store: Store):
    await store.ensure_points(1, 1, 0)
    ok, balance, _ = await store.claim_daily(1, 1, "2026-01-01", "sign", 10)
    assert ok and balance == 10
    ok, balance, text = await store.claim_daily(1, 1, "2026-01-01", "sign", 10)
    assert not ok and balance == 10 and "领过" in text
    ok, balance, _ = await store.claim_daily(1, 1, "2026-01-02", "sign", 10)
    assert ok and balance == 20

    await store.ensure_points(1, 2, 100)
    ok, balance, text = await store.claim_daily(
        1, 2, "2026-01-01", "relief", 50, threshold=20
    )
    assert not ok and balance == 100 and "还没到" in text


async def test_sign_streak_counts_consecutive_days(store: Store):
    await store.ensure_points(1, 1, 0)
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        assert (await store.claim_daily(1, 1, day, "sign", 10))[0]
    assert await store.sign_streak(1, 1, "2026-01-04") == 3
    assert await store.sign_streak(1, 1, "2026-01-05") == 0  # 中间断了
    assert await store.sign_streak(1, 1, "2026-01-01") == 0  # 今天之前没有


# ---------------------------------------------------------------- 迁移

async def test_old_roulette_tables_migrate_to_points(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE roulette_points (
            group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0, updated_at TEXT,
            PRIMARY KEY (group_id, user_id)
        );
        CREATE TABLE roulette_relief (
            group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            day TEXT NOT NULL, PRIMARY KEY (group_id, user_id, day)
        );
        INSERT INTO roulette_points VALUES (1, 2, 50, '2026-01-01');
        INSERT INTO roulette_relief VALUES (1, 2, '2026-01-01');
        """
    )
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert await store.get_points(1, 2) == 50
        history = await store.points_history(1, 2)
        assert history[0]["reason"] == "migrate" and history[0]["delta"] == 50
        assert await store.daily_claimed(1, 2, "2026-01-01", "relief")
        conn = await store._ensure()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name IN ('roulette_points', 'roulette_relief')"
        ) as cur:
            assert await cur.fetchall() == []
    finally:
        await store.close()


# ---------------------------------------------------------------- 插件行为

async def test_sign_in_rewards_streak_and_is_idempotent(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await store.ensure_points(1, 1, 100)
    assert (await store.claim_daily(1, 1, yesterday, "sign", 10))[0]

    ok, message = await points.sign_in(1, 1)
    assert ok
    text = message.extract_plain_text()
    assert "+12" in text and "连续第 2 天" in text
    assert await store.get_points(1, 1) == 122

    ok, message = await points.sign_in(1, 1)
    assert not ok and "已经签过" in message.extract_plain_text()


async def test_relief_respects_threshold_and_daily_limit(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    await store.ensure_points(1, 1, 0)
    await store.set_kv("points_relief_threshold", "100")
    ok, message = await points.relief_message(1, 1)
    assert ok and "50" in message.extract_plain_text()
    ok, message = await points.relief_message(1, 1)
    assert not ok and "领过" in message.extract_plain_text()
    assert await store.get_points(1, 1) == 50


async def test_adjust_points_writes_admin_ledger(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    ok, text = await points.adjust_points(1, 9, 2, 30, "活动奖励")
    assert ok and "当前 30" in text
    assert await store.get_points(1, 2) == 30
    [row] = await store.points_history(1, 2)
    assert row["reason"] == "admin" and row["note"].startswith("操作人 9")


async def test_update_setting_clamps(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    ok, text = await points.update_setting("签到基础", "15")
    assert ok and "15" in text
    assert (await points.load_settings())["sign_base"] == 15
    ok, text = await points.update_setting("签到基础", "999999")
    assert ok
    assert (await points.load_settings())["sign_base"] == 1000
    ok, text = await points.update_setting("不存在的键", "1")
    assert not ok and "可配置项" in text


async def test_points_messages(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    text = (await points.points_message(1, 1)).extract_plain_text()
    assert "积分" in text and "今日未签到" in text
    assert (await points.sign_in(1, 1))[0]
    text = (await points.points_message(1, 1)).extract_plain_text()
    assert "今日已签到" in text and "连续第 1 天" in text
    board = (await points.leaderboard_message(1)).extract_plain_text()
    assert "积分排行" in board and "110" in board
    history = (await points.history_message(1, 1)).extract_plain_text()
    assert "开户" in history and "每日" in history


async def test_panel_aggregates(store: Store):
    today = date.today().isoformat()
    await store.ensure_points(1, 1, 100)
    await store.ensure_points(1, 2, 50)
    assert (await store.claim_daily(1, 1, today, "sign", 10))[0]
    overview = await store.points_overview(1, today)
    assert overview == {"accounts": 2, "total": 160, "sign_today": 1, "ledger": 3}
    accounts = await store.points_accounts(1)
    assert accounts[0]["user_id"] == 1 and accounts[0]["balance"] == 110
    ledger = await store.recent_points_ledger(1, 10)
    assert len(ledger) == 3
    assert ledger[0]["user_id"] == 1 and ledger[0]["reason"] == "daily"


async def test_store_transfer_burns_fee_and_writes_ledger(store: Store):
    await store.ensure_points(1, 1, 100)
    ok, from_balance, to_balance, text = await store.transfer_points(1, 1, 2, 50, 3)
    assert ok and from_balance == 47 and to_balance == 50
    assert "手续费 3" in text
    sender = await store.points_history(1, 1)
    receiver = await store.points_history(1, 2)
    assert sender[0]["delta"] == -53 and sender[0]["reason"] == "transfer"
    assert receiver[0]["delta"] == 50 and receiver[0]["reason"] == "transfer"
    assert (await store.points_overview(1, "2026-01-01"))["total"] == 97  # 手续费销毁


async def test_store_transfer_insufficient_leaves_balances_unchanged(store: Store):
    await store.ensure_points(1, 1, 10)
    ok, from_balance, _, _ = await store.transfer_points(1, 1, 2, 20, 1)
    assert not ok and from_balance == 10
    assert await store.get_points(1, 1) == 10
    assert await store.get_points(1, 2) is None


async def test_plugin_transfer_uses_configured_fee_and_blocks_self(store: Store, monkeypatch):
    monkeypatch.setattr(points, "get_store", lambda: store)
    await store.ensure_points(1, 1, 100)
    ok, text = await points.transfer(1, 1, 2, 50)
    assert ok and "手续费 3" in text  # 5% 向上取整
    assert await store.get_points(1, 1) == 47
    assert await store.get_points(1, 2) == 50
    ok, text = await points.transfer(1, 1, 1, 10)
    assert not ok and "自己" in text


async def test_concurrent_transfers_do_not_go_negative(store: Store):
    await store.ensure_points(1, 1, 100)
    results = await asyncio.gather(
        store.transfer_points(1, 1, 2, 60, 0),
        store.transfer_points(1, 1, 3, 60, 0),
    )
    assert sum(1 for ok, *_ in results if ok) == 1
    assert await store.get_points(1, 1) == 40
