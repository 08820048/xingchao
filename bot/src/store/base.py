"""连接管理与 kv：所有领域 mixin 的公共基类。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import aiosqlite
from nonebot.log import logger

from src.store.schema import _SCHEMA, _migrate


class StoreBase:
    """惰性连接的 aiosqlite 封装。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        # kv 是读多写少的配置表（模块开关、面板设置），每条群消息会被十几个插件反复读。
        # 所有写入都经过 set_kv，因此用写穿透缓存：读命中内存，写同时更新库和缓存。
        # None 也缓存（表示「没有这个键」），避免对未配置项反复查库。
        self._kv_cache: dict[str, Optional[str]] = {}

    async def _ensure(self) -> aiosqlite.Connection:
        async with self._lock:
            if self._conn is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(self._path)
                # WAL：读写互不阻塞，commit 不再每次整库 fsync；NORMAL 在 WAL 下断电只丢最后
                # 几个事务、不会损坏库，对统计/配置足够。备份用的是 sqlite3 backup API，WAL 下依然一致。
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA synchronous=NORMAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.executescript(_SCHEMA)
                await _migrate(conn)
                await conn.commit()
                self._conn = conn
                logger.debug(f"SQLite 已连接：{self._path}")
            return self._conn

    async def get_kv(self, key: str) -> Optional[str]:
        if key in self._kv_cache:
            return self._kv_cache[key]
        conn = await self._ensure()
        async with conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        value = row[0] if row else None
        self._kv_cache[key] = value
        return value

    async def set_kv(self, key: str, value: str) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()
        self._kv_cache[key] = value

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._kv_cache.clear()
