"""表结构与版本化迁移。"""

from __future__ import annotations

from datetime import datetime

import aiosqlite
from nonebot.log import logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS msg_stat (
    group_id INTEGER,
    day TEXT,
    count INTEGER,
    PRIMARY KEY (group_id, day)
);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    time TEXT NOT NULL,
    message TEXT NOT NULL,
    at_all INTEGER DEFAULT 0,
    repeat TEXT DEFAULT 'daily',
    weekday INTEGER,
    date TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS feed_subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    time TEXT NOT NULL,
    repeat TEXT DEFAULT 'daily',
    weekday INTEGER,
    params TEXT DEFAULT '{}',
    ai_summary INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    group_id INTEGER,
    due_ts INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS memories (
    scope TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    facts TEXT NOT NULL DEFAULT '[]',
    interactions INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (scope, group_id, user_id)
);
CREATE TABLE IF NOT EXISTS memory_optout (
    user_id INTEGER PRIMARY KEY,
    ts TEXT
);
CREATE TABLE IF NOT EXISTS msg_stat_user (
    group_id INTEGER,
    day TEXT,
    user_id INTEGER,
    count INTEGER,
    PRIMARY KEY (group_id, day, user_id)
);
CREATE TABLE IF NOT EXISTS punishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    action TEXT NOT NULL,
    mute_minutes INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    source TEXT DEFAULT 'sensitive',
    appeal_status TEXT DEFAULT 'none',
    appeal_text TEXT DEFAULT '',
    appeal_ts TEXT
);
CREATE TABLE IF NOT EXISTS word_stat (
    word TEXT PRIMARY KEY,
    candidates INTEGER DEFAULT 0,
    released INTEGER DEFAULT 0,
    punished INTEGER DEFAULT 0,
    fallback INTEGER DEFAULT 0,
    last_ts TEXT
);
CREATE TABLE IF NOT EXISTS blog_posts (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    category TEXT DEFAULT '',
    published TEXT DEFAULT '',
    excerpt TEXT DEFAULT '',
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    keywords TEXT DEFAULT '',
    hits INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_by INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_group ON kb_entries (group_id);
CREATE TABLE IF NOT EXISTS action_stat (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (day, group_id, action)
);
CREATE TABLE IF NOT EXISTS bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT DEFAULT '',
    duration_seconds INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bot_events_ts ON bot_events (ts);
CREATE TABLE IF NOT EXISTS stickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha TEXT NOT NULL UNIQUE,
    file TEXT NOT NULL,
    mime TEXT NOT NULL,
    text TEXT DEFAULT '',
    meaning TEXT DEFAULT '',
    usage TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    group_id INTEGER NOT NULL DEFAULT 0,
    saved_by INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'cmd',
    uses INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS points_accounts (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref TEXT,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_ledger_group
    ON points_ledger (group_id, id);
CREATE INDEX IF NOT EXISTS idx_points_ledger_user
    ON points_ledger (group_id, user_id, id);
CREATE TABLE IF NOT EXISTS points_daily (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id, day, kind)
);
CREATE TABLE IF NOT EXISTS roulette_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    victim_id INTEGER NOT NULL,
    player_count INTEGER NOT NULL,
    stake INTEGER NOT NULL,
    bonus INTEGER NOT NULL,
    deltas TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_roulette_games_group
    ON roulette_games (group_id, id);
CREATE TABLE IF NOT EXISTS shop_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    price INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT -1,
    per_user_limit INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    delivery TEXT NOT NULL DEFAULT 'manual',
    delivery_data TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shop_items_group
    ON shop_items (group_id, enabled, id);
CREATE TABLE IF NOT EXISTS shop_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    user_note TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_by TEXT DEFAULT '',
    resolved_at TEXT,
    resolve_note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_shop_redemptions_group
    ON shop_redemptions (group_id, id);
CREATE INDEX IF NOT EXISTS idx_shop_redemptions_user
    ON shop_redemptions (group_id, user_id, id);
CREATE INDEX IF NOT EXISTS idx_shop_redemptions_status
    ON shop_redemptions (group_id, status, id);
"""


async def _add_columns(conn: aiosqlite.Connection, table: str, cols: tuple[tuple[str, str], ...]) -> None:
    """幂等加列：已存在的列跳过（老库可能已经被旧的手写迁移加过）。"""
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        existing = {r[1] for r in await cur.fetchall()}
    for col, ddl in cols:
        if col not in existing:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


async def _m1_punishment_columns(conn: aiosqlite.Connection) -> None:
    # counted=是否计入升级；message_id=被处罚消息，用于申诉时读日志原文；
    # appeal_note/resolved_by=处理备注与处理人；appeal_reminded=是否已催办
    await _add_columns(conn, "punishments", (
        ("counted", "INTEGER DEFAULT 1"),
        ("message_id", "INTEGER"),
        ("appeal_note", "TEXT DEFAULT ''"),
        ("resolved_by", "TEXT DEFAULT ''"),
        ("appeal_reminded", "INTEGER DEFAULT 0"),
    ))


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ) as cur:
        return await cur.fetchone() is not None


async def _m2_points_ledger(conn: aiosqlite.Connection) -> None:
    """把旧的轮盘筹码/补助合并进通用积分账本，然后删掉旧表。

    - roulette_points → points_accounts + points_ledger（reason=migrate）
    - roulette_relief → points_daily（kind=relief，amount=0）
    - roulette_games 保留，继续作为对局审计
    """
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if await _table_exists(conn, "roulette_points"):
        async with conn.execute(
            "SELECT group_id, user_id, points FROM roulette_points"
        ) as cur:
            rows = await cur.fetchall()
        for group_id, user_id, points in rows:
            cur = await conn.execute(
                "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id, user_id) DO NOTHING",
                (group_id, user_id, int(points), now, now),
            )
            if cur.rowcount:
                await conn.execute(
                    "INSERT INTO points_ledger"
                    " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                    " VALUES (?, ?, ?, ?, 'migrate', 'roulette_points', '旧轮盘筹码迁移', ?)",
                    (group_id, user_id, int(points), int(points), now),
                )
        await conn.execute("DROP TABLE roulette_points")
    if await _table_exists(conn, "roulette_relief"):
        async with conn.execute(
            "SELECT group_id, user_id, day FROM roulette_relief"
        ) as cur:
            rows = await cur.fetchall()
        for group_id, user_id, day in rows:
            await conn.execute(
                "INSERT INTO points_daily"
                " (group_id, user_id, day, kind, amount, created_at)"
                " VALUES (?, ?, ?, 'relief', 0, ?)"
                " ON CONFLICT(group_id, user_id, day, kind) DO NOTHING",
                (group_id, user_id, day, now),
            )
        await conn.execute("DROP TABLE roulette_relief")


# 结构迁移：_SCHEMA 只放「建表」（CREATE ... IF NOT EXISTS），对已有表的改动按顺序追加到这里。
# 第 i 个（从 1 开始）执行完后 PRAGMA user_version = i；已执行过的不会再跑。只能追加，不要改旧条目。
_MIGRATIONS: tuple = (
    _m1_punishment_columns,
    _m2_points_ledger,
)


async def _migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cur:
        version = int((await cur.fetchone())[0])
    for i, step in enumerate(_MIGRATIONS[version:], start=version + 1):
        await step(conn)
        await conn.execute(f"PRAGMA user_version = {i}")
        await conn.commit()
        logger.info(f"数据库结构已迁移到 v{i}（{step.__name__}）")
