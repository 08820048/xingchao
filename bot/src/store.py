"""SQLite 存储层（aiosqlite）。只存 kv 与统计，禁止写聊天内容。"""

from __future__ import annotations

import asyncio
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
