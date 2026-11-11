import sqlite3

from src.store import Store


async def test_count_strikes_ignores_uncounted_and_accepted_appeals(store: Store):
    await store.add_punishment(1, 2, "w", "recall")
    await store.add_punishment(1, 2, "w", "recall", counted=False)  # 兜底撤回不计入
    pid = await store.add_punishment(1, 2, "w", "mute", 10)
    await store.add_punishment(1, 3, "w", "recall")  # 别人
    await store.add_punishment(9, 2, "w", "recall")  # 别的群
    assert await store.count_strikes(1, 2) == 2
    assert await store.count_strikes(9, 2) == 1

    assert await store.appeal_punishment(pid, "我是正常讨论")
    assert await store.resolve_appeal(pid, accept=True) is not None
    assert await store.count_strikes(1, 2) == 1  # 申诉通过后不再计数


async def test_rejected_appeal_still_counts(store: Store):
    pid = await store.add_punishment(1, 2, "w", "recall")
    await store.appeal_punishment(pid, "x")
    await store.resolve_appeal(pid, accept=False)
    assert await store.count_strikes(1, 2) == 1


async def test_count_strikes_window(store: Store):
    await store.add_punishment(1, 2, "w", "recall")
    conn = await store._ensure()
    await conn.execute("UPDATE punishments SET ts = '2000-01-01T00:00:00+08:00'")
    await conn.commit()
    assert await store.count_strikes(1, 2, days=30) == 0


async def test_count_actions_today_only_counts_today_and_group(store: Store):
    await store.add_punishment(1, 2, "w", "kick")
    await store.add_punishment(1, 3, "w", "kick")
    await store.add_punishment(1, 4, "w", "recall")
    await store.add_punishment(2, 5, "w", "kick")
    assert await store.count_actions_today(1, "kick") == 2
    assert await store.count_actions_today(2, "kick") == 1
    conn = await store._ensure()
    await conn.execute("UPDATE punishments SET ts = '2000-01-01T00:00:00+08:00' WHERE user_id = 2")
    await conn.commit()
    assert await store.count_actions_today(1, "kick") == 1


async def test_word_stats_and_appeal_join(store: Store):
    for outcome in ["released"] * 4 + ["punished", "fallback"]:
        await store.incr_word_stat("机场", outcome)
    await store.incr_word_stat("机场", "bogus")  # 非法类型应被忽略
    pid = await store.add_punishment(1, 2, "机场", "recall")
    await store.appeal_punishment(pid, "x")
    await store.resolve_appeal(pid, accept=True)
    [row] = await store.word_stats()
    assert (row["candidates"], row["released"], row["punished"], row["fallback"]) == (6, 4, 1, 1)
    assert (row["appeal_accepted"], row["appeals"]) == (1, 1)


async def test_old_database_is_migrated_with_counted_column(tmp_path):
    """线上旧库没有 counted 列：打开时必须自动补上，且旧记录默认计入。"""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE punishments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, word TEXT NOT NULL, action TEXT NOT NULL,
            mute_minutes INTEGER DEFAULT 0, reason TEXT DEFAULT '', source TEXT DEFAULT 'sensitive',
            appeal_status TEXT DEFAULT 'none', appeal_text TEXT DEFAULT '', appeal_ts TEXT);
        """
    )
    import datetime

    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("INSERT INTO punishments (ts, group_id, user_id, word, action) VALUES (?,1,2,'w','recall')", (now,))
    db.commit()
    db.close()

    store = Store(path)
    assert await store.count_strikes(1, 2) == 1
    conn = await store._ensure()
    async with conn.execute("PRAGMA table_info(punishments)") as cur:
        assert "counted" in {r[1] for r in await cur.fetchall()}
    await store.close()
