"""表结构与版本化迁移。"""

from __future__ import annotations

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


# 结构迁移：_SCHEMA 只放「建表」（CREATE ... IF NOT EXISTS），对已有表的改动按顺序追加到这里。
# 第 i 个（从 1 开始）执行完后 PRAGMA user_version = i；已执行过的不会再跑。只能追加，不要改旧条目。
_MIGRATIONS: tuple = (
    _m1_punishment_columns,
)


async def _migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cur:
        version = int((await cur.fetchone())[0])
    for i, step in enumerate(_MIGRATIONS[version:], start=version + 1):
        await step(conn)
        await conn.execute(f"PRAGMA user_version = {i}")
        await conn.commit()
        logger.info(f"数据库结构已迁移到 v{i}（{step.__name__}）")
