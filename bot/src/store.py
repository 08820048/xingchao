"""SQLite 存储层（aiosqlite）。只存 kv 与统计，禁止写聊天内容。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
from nonebot import get_driver
from nonebot.log import logger

from src.config import get_config

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
CREATE TABLE IF NOT EXISTS msg_stat_user (
    group_id INTEGER,
    day TEXT,
    user_id INTEGER,
    count INTEGER,
    PRIMARY KEY (group_id, day, user_id)
);
"""


class Store:
    """惰性连接的 aiosqlite 封装。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> aiosqlite.Connection:
        async with self._lock:
            if self._conn is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(self._path)
                await conn.executescript(_SCHEMA)
                await conn.commit()
                self._conn = conn
                logger.debug(f"SQLite 已连接：{self._path}")
            return self._conn

    async def get_kv(self, key: str) -> Optional[str]:
        conn = await self._ensure()
        async with conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_kv(self, key: str, value: str) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()

    async def incr_msg_stat(self, group_id: int, day: str) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO msg_stat (group_id, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(group_id, day) DO UPDATE SET count = count + 1",
            (group_id, day),
        )
        await conn.commit()

    async def incr_user_msg_stat(self, group_id: int, day: str, user_id: int) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO msg_stat_user (group_id, day, user_id, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(group_id, day, user_id) DO UPDATE SET count = count + 1",
            (group_id, day, user_id),
        )
        await conn.commit()

    async def get_group_day_stat(self, group_id: int, day: str) -> tuple[int, int]:
        """返回 (总消息数, 参与人数)。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COALESCE(SUM(count), 0), COUNT(*) FROM msg_stat_user "
            "WHERE group_id = ? AND day = ?",
            (group_id, day),
        ) as cur:
            row = await cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def get_top_users(
        self, group_id: int, day: str, limit: int = 5
    ) -> list[tuple[int, int]]:
        """返回 [(user_id, count)]，按发言数降序。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, count FROM msg_stat_user "
            "WHERE group_id = ? AND day = ? ORDER BY count DESC, user_id LIMIT ?",
            (group_id, day, limit),
        ) as cur:
            return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    async def add_task(self, group_id: int, time_: str, message: str, at_all: bool,
                       repeat: str, weekday: int | None, date_: str | None) -> int:
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO scheduled_tasks (group_id, time, message, at_all, repeat, weekday, date, enabled, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (group_id, time_, message, int(at_all), repeat, weekday, date_,
             datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_tasks(self, enabled_only: bool = False) -> list[dict]:
        conn = await self._ensure()
        sql = "SELECT id, group_id, time, message, at_all, repeat, weekday, date, enabled FROM scheduled_tasks"
        if enabled_only:
            sql += " WHERE enabled = 1"
        async with conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "group_id": r[1], "time": r[2], "message": r[3],
             "at_all": bool(r[4]), "repeat": r[5], "weekday": r[6], "date": r[7],
             "enabled": bool(r[8])}
            for r in rows
        ]

    async def update_task(self, task_id: int, **fields) -> None:
        allowed = {"group_id", "time", "message", "at_all", "repeat", "weekday", "date", "enabled"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(int(v) if isinstance(v, bool) else v)
        if not sets:
            return
        vals.append(task_id)
        conn = await self._ensure()
        await conn.execute(f"UPDATE scheduled_tasks SET {', '.join(sets)} WHERE id = ?", vals)
        await conn.commit()

    async def delete_task(self, task_id: int) -> bool:
        conn = await self._ensure()
        cur = await conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await conn.commit()
        return cur.rowcount > 0

    async def get_day_overview(self, day: str) -> list[tuple[int, int]]:
        """返回当日所有群 [(group_id, 总消息数)]，按消息数降序。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT group_id, COALESCE(SUM(count), 0) FROM msg_stat "
            "WHERE day = ? GROUP BY group_id ORDER BY SUM(count) DESC",
            (day,),
        ) as cur:
            return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


_store: Optional[Store] = None
_shutdown_hooked: bool = False


def get_store() -> Store:
    """惰性单例：首次调用需在 nonebot.init() 之后。"""
    global _store, _shutdown_hooked
    if _store is None:
        _store = Store(get_config().xingchao_db_path)
    if not _shutdown_hooked:
        get_driver().on_shutdown(_store.close)
        _shutdown_hooked = True
    return _store
