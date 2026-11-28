"""轮盘积分局：赔率、随机枪位、状态机、存储与白名单闸门。"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from src.plugins import points
from src.plugins import roulette
from src.plugins import whitelist as whitelist_plugin
from src.store import Store


class FakeBot:
    self_id = "1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_api(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        return {}


def _no_spawn(coro, name):
    """测试里不让大厅/回合超时任务真的睡 45–60 秒。"""
    coro.close()
    return None


@pytest.fixture(autouse=True)
def _reset_games():
    roulette._games.clear()
    roulette._last_end.clear()
    roulette._locks.clear()
    yield
    roulette._games.clear()
    roulette._last_end.clear()
    roulette._locks.clear()


# ---------------------------------------------------------------- 纯规则

def test_compute_deltas_creator_hit_is_zero_sum():
    deltas = roulette.compute_deltas(creator=1, victim=1, players=[1, 2, 3, 4], stake=5, bonus=1)
    assert deltas == {1: -15, 2: 5, 3: 5, 4: 5}
    assert sum(deltas.values()) == 0


def test_compute_deltas_challenger_hit_pays_creator():
    deltas = roulette.compute_deltas(creator=1, victim=2, players=[1, 2, 3, 4], stake=5, bonus=1)
    assert deltas == {1: 6, 2: -6}
    assert sum(deltas.values()) == 0


def test_build_slots_gives_every_player_equal_victim_chance():
    """命中位均匀 + 枪位随机 => 每个玩家的中枪概率都是 1/N。"""
    rng = random.Random(20261124)
    players = [10, 20, 30, 40]
    counts: Counter[int] = Counter()
    rounds = 20_000
    for _ in range(rounds):
        slots, hit_index = roulette.build_slots(players, rng)
        assert len(slots) == 6 and 0 <= hit_index < 6
        counts[slots[hit_index]] += 1
    for user_id in players:
        assert abs(counts[user_id] - rounds / len(players)) < rounds * 0.03


# ---------------------------------------------------------------- 存储

async def test_store_settlement_and_history(store: Store):
    for user_id in (1, 2, 3):
        assert await store.ensure_points(1, user_id, 100) == 100
    balances = await store.settle_roulette(
        group_id=1, creator_id=1, victim_id=2, player_count=3,
        stake=5, bonus=1, deltas={1: 6, 2: -6},
    )
    assert balances == {1: 106, 2: 94}
    assert await store.get_points(1, 1) == 106
    assert await store.get_points(1, 3) == 100
    stats = await store.roulette_stats(1, 1)
    assert (stats["games"], stats["as_creator"], stats["creator_wins"]) == (1, 1, 1)
    assert (await store.roulette_stats(1, 2))["as_victim"] == 1
    [game] = await store.recent_roulette_games(1)
    assert game["victim_id"] == 2 and game["player_count"] == 3
    assert game["deltas"] == {"1": 6, "2": -6}


async def test_store_rejects_unbalanced_settlement(store: Store):
    await store.ensure_points(1, 1, 100)
    with pytest.raises(ValueError):
        await store.settle_roulette(1, 1, 1, 1, 5, 1, {1: 1})


# ---------------------------------------------------------------- 状态机

async def test_game_challenger_hit_settles_and_clears(store: Store, monkeypatch):
    monkeypatch.setattr(roulette, "get_store", lambda: store)
    monkeypatch.setattr(points, "get_store", lambda: store)
    monkeypatch.setattr(roulette, "spawn", _no_spawn)
    bot = FakeBot()

    ok, _ = await roulette.start_lobby(bot, 1, 1)
    assert ok
    assert (await roulette.join_lobby(bot, 1, 2))[0]
    assert (await roulette.join_lobby(bot, 1, 2))[0] is False  # 不能重复上车
    assert (await roulette.join_lobby(bot, 1, 3))[0]
    assert (await roulette.begin_game(bot, 1, 1))[0]

    game = roulette._games[1]
    game.slots = [1, 2, 3, 1, 2, 3]
    game.hit_index = 1
    game.turn = 0

    # 第 1 枪：庄家安全，轮到 2
    assert (await roulette.shoot(bot, 1, 1))[0]
    assert roulette._games[1].turn == 1
    # 不是 2 的回合不能替别人开枪
    assert (await roulette.shoot(bot, 1, 3))[0] is False
    # 第 2 枪：2 中枪，庄家 +6，2 -6
    assert (await roulette.shoot(bot, 1, 2))[0]
    assert 1 not in roulette._games
    assert await store.get_points(1, 1) == 106
    assert await store.get_points(1, 2) == 94
    assert await store.get_points(1, 3) == 100


async def test_game_creator_hit_pays_everyone(store: Store, monkeypatch):
    monkeypatch.setattr(roulette, "get_store", lambda: store)
    monkeypatch.setattr(points, "get_store", lambda: store)
    monkeypatch.setattr(roulette, "spawn", _no_spawn)
    bot = FakeBot()

    assert (await roulette.start_lobby(bot, 1, 1))[0]
    assert (await roulette.join_lobby(bot, 1, 2))[0]
    assert (await roulette.join_lobby(bot, 1, 3))[0]
    assert (await roulette.begin_game(bot, 1, 1))[0]
    game = roulette._games[1]
    game.slots = [1, 2, 3, 1, 2, 3]
    game.hit_index = 0
    game.turn = 0

    assert (await roulette.shoot(bot, 1, 1))[0]
    assert 1 not in roulette._games
    assert await store.get_points(1, 1) == 90  # 赔 5×2
    assert await store.get_points(1, 2) == 105
    assert await store.get_points(1, 3) == 105


async def test_lobby_cancel_only_creator_or_superuser(store: Store, monkeypatch):
    monkeypatch.setattr(roulette, "get_store", lambda: store)
    monkeypatch.setattr(points, "get_store", lambda: store)
    monkeypatch.setattr(roulette, "spawn", _no_spawn)
    bot = FakeBot()
    assert (await roulette.start_lobby(bot, 1, 1))[0]
    # conftest 里 1/2 是超管，用 3 验证普通成员不能取消
    assert (await roulette.cancel_lobby(bot, 1, 3))[0] is False
    assert (await roulette.cancel_lobby(bot, 1, 1))[0]
    assert 1 not in roulette._games


# ---------------------------------------------------------------- 白名单闸门

class FakeEvent:
    def __init__(self, group_id=9, user_id=3):
        self.group_id = group_id
        self.user_id = user_id
        self.stopped = False

    def stop_propagation(self):
        self.stopped = True


async def test_preprocessor_blocks_non_whitelist_group(monkeypatch):
    monkeypatch.setattr(whitelist_plugin, "merged_whitelist", lambda: {1})
    monkeypatch.setattr(whitelist_plugin, "disabled_groups", lambda: set())
    event = FakeEvent(group_id=9, user_id=3)
    await whitelist_plugin.block_non_whitelist(event)
    assert event.stopped


async def test_preprocessor_allows_whitelist_and_superuser(monkeypatch):
    monkeypatch.setattr(whitelist_plugin, "merged_whitelist", lambda: {1})
    monkeypatch.setattr(whitelist_plugin, "disabled_groups", lambda: set())
    allowed = FakeEvent(group_id=1, user_id=3)
    await whitelist_plugin.block_non_whitelist(allowed)
    assert not allowed.stopped
    superuser = FakeEvent(group_id=9, user_id=1)  # conftest 里 SUPERUSERS={"1","2"}
    await whitelist_plugin.block_non_whitelist(superuser)
    assert not superuser.stopped
