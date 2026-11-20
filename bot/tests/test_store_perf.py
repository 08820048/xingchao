"""存储层性能相关行为：WAL、kv 写穿透缓存、合并的发言计数；后台 spawn。"""

import asyncio


async def test_wal_enabled(store):
    conn = await store._ensure()
    async with conn.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0].lower() == "wal"


async def test_kv_cache_write_through(store):
    assert await store.get_kv("k") is None  # 缺失也会被缓存
    await store.set_kv("k", "v1")
    assert await store.get_kv("k") == "v1"
    await store.set_kv("k", "v2")
    assert await store.get_kv("k") == "v2"
    # 缓存与库一致：清缓存后从库读出的也是新值
    store._kv_cache.clear()
    assert await store.get_kv("k") == "v2"


async def test_kv_cache_avoids_db_reads(store):
    await store.set_kv("k", "v")
    conn = await store._ensure()
    calls = 0
    orig = conn.execute

    def counting(*a, **kw):
        nonlocal calls
        calls += 1
        return orig(*a, **kw)

    conn.execute = counting
    for _ in range(10):
        assert await store.get_kv("k") == "v"
        assert await store.get_kv("missing") is None
    conn.execute = orig
    assert calls == 1  # 只有第一次查 "missing" 落库


async def test_incr_msg_stats(store):
    for uid in (1, 1, 2):
        await store.incr_msg_stats(100, "2026-11-20", uid)
    assert await store.get_group_day_stat(100, "2026-11-20") == (3, 2)
    assert await store.get_top_users(100, "2026-11-20") == [(1, 2), (2, 1)]
    conn = await store._ensure()
    async with conn.execute("SELECT count FROM msg_stat WHERE group_id = 100") as cur:
        assert (await cur.fetchone())[0] == 3


async def test_spawn_keeps_reference_and_logs(monkeypatch):
    from src import background

    errors = []
    monkeypatch.setattr(
        background.logger, "opt", lambda **kw: type("L", (), {"error": lambda _s, m: errors.append(m)})()
    )

    async def ok():
        await asyncio.sleep(0)

    async def boom():
        raise RuntimeError("x")

    t1 = background.spawn(ok(), "ok")
    t2 = background.spawn(boom(), "boom")
    assert t1 in background._detached and t2 in background._detached
    await asyncio.gather(t1, t2, return_exceptions=True)
    await asyncio.sleep(0)
    assert not background._detached
    assert errors and "boom" in errors[0]
