"""SQLite 存储层（aiosqlite）。只存 kv 与统计，禁止写聊天内容。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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
CREATE TABLE IF NOT EXISTS blog_posts (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    category TEXT DEFAULT '',
    published TEXT DEFAULT '',
    excerpt TEXT DEFAULT '',
    fetched_at TEXT NOT NULL
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

    async def get_day_series(
        self, start: str, end: str, group_id: int | None = None
    ) -> list[tuple[str, int, int]]:
        """按天返回 [(day, 消息数, 参与人数)] 升序；group_id 为空时汇总全部群。"""
        conn = await self._ensure()
        where = "day BETWEEN ? AND ?"
        params: list[str | int] = [start, end]
        if group_id is not None:
            where += " AND group_id = ?"
            params.append(group_id)
        async with conn.execute(
            f"SELECT day, COALESCE(SUM(count), 0) FROM msg_stat WHERE {where} GROUP BY day",
            params,
        ) as cur:
            totals = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
        async with conn.execute(
            f"SELECT day, COUNT(DISTINCT user_id) FROM msg_stat_user "
            f"WHERE {where} GROUP BY day",
            params,
        ) as cur:
            users = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
        return [(d, totals.get(d, 0), users.get(d, 0)) for d in sorted(set(totals) | set(users))]

    async def get_top_users_all(self, day: str, limit: int = 10) -> list[tuple[int, int]]:
        """返回当天跨群发言 Top [(user_id, count)]，按发言数降序。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, SUM(count) FROM msg_stat_user WHERE day = ? "
            "GROUP BY user_id ORDER BY SUM(count) DESC, user_id LIMIT ?",
            (day, limit),
        ) as cur:
            return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    async def get_range_users(self, start: str, end: str, group_id: int | None = None) -> int:
        """区间内去重参与人数；group_id 为空时跨群统计。"""
        conn = await self._ensure()
        where = "day BETWEEN ? AND ?"
        params: list[str | int] = [start, end]
        if group_id is not None:
            where += " AND group_id = ?"
            params.append(group_id)
        async with conn.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM msg_stat_user WHERE {where}", params
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_top_users_range(
        self, start: str, end: str, group_id: int | None = None, limit: int = 5
    ) -> list[tuple[int, int]]:
        """区间内发言 Top；group_id 为空时跨群统计。"""
        conn = await self._ensure()
        where = "day BETWEEN ? AND ?"
        params: list[str | int] = [start, end]
        if group_id is not None:
            where += " AND group_id = ?"
            params.append(group_id)
        async with conn.execute(
            f"SELECT user_id, SUM(count) FROM msg_stat_user WHERE {where} "
            "GROUP BY user_id ORDER BY SUM(count) DESC, user_id LIMIT ?",
            [*params, limit],
        ) as cur:
            return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    # ------------------------------------------------------------ 违规记录 / 申诉

    async def add_punishment(
        self,
        group_id: int,
        user_id: int,
        word: str,
        action: str,
        mute_minutes: int = 0,
        reason: str = "",
        source: str = "sensitive",
    ) -> int:
        """记录一次处罚（不存聊天内容，只存命中词与处理结果）。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO punishments (ts, group_id, user_id, word, action, mute_minutes,"
            " reason, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                group_id, user_id, word, action, mute_minutes, reason[:200], source,
            ),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_punishments(
        self, limit: int = 100, group_id: int | None = None
    ) -> list[dict]:
        conn = await self._ensure()
        sql = (
            "SELECT id, ts, group_id, user_id, word, action, mute_minutes, reason,"
            " appeal_status, appeal_text, source FROM punishments"
        )
        params: list[int] = []
        if group_id is not None:
            sql += " WHERE group_id = ?"
            params.append(group_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "group_id": r[2], "user_id": r[3], "word": r[4],
                "action": r[5], "mute_minutes": r[6], "reason": r[7],
                "appeal_status": r[8], "appeal_text": r[9], "source": r[10],
            }
            for r in rows
        ]

    async def repeat_offenders(self, days: int = 30, limit: int = 10) -> list[dict]:
        """近 N 天被处罚次数排行。"""
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, COUNT(*), COUNT(DISTINCT group_id) FROM punishments"
            " WHERE ts >= ? GROUP BY user_id ORDER BY COUNT(*) DESC, user_id LIMIT ?",
            (since, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [{"user_id": r[0], "count": r[1], "groups": r[2]} for r in rows]

    async def latest_punishment_for(self, user_id: int, hours: int = 24) -> dict | None:
        since = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, ts, group_id, word, action, appeal_status FROM punishments"
            " WHERE user_id = ? AND ts >= ? ORDER BY id DESC LIMIT 1",
            (user_id, since),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "ts": row[1], "group_id": row[2], "word": row[3],
            "action": row[4], "appeal_status": row[5],
        }

    async def appeal_punishment(self, punishment_id: int, text: str) -> bool:
        """提交申诉；仅当该记录尚未申诉过。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "UPDATE punishments SET appeal_status = 'pending', appeal_text = ?, appeal_ts = ?"
            " WHERE id = ? AND appeal_status = 'none'",
            (
                text[:300],
                datetime.now().astimezone().isoformat(timespec="seconds"),
                punishment_id,
            ),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def resolve_appeal(self, punishment_id: int, accept: bool) -> dict | None:
        """处理申诉，返回该记录（含群/用户，便于撤销禁言）；非待处理记录返回 None。"""
        conn = await self._ensure()
        status = "accepted" if accept else "rejected"
        cur = await conn.execute(
            "UPDATE punishments SET appeal_status = ? WHERE id = ? AND appeal_status = 'pending'",
            (status, punishment_id),
        )
        await conn.commit()
        if cur.rowcount == 0:
            return None
        async with conn.execute(
            "SELECT id, group_id, user_id, word, action, mute_minutes"
            " FROM punishments WHERE id = ?",
            (punishment_id,),
        ) as cur2:
            row = await cur2.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "group_id": row[1], "user_id": row[2],
            "word": row[3], "action": row[4], "mute_minutes": row[5],
        }

    async def pending_appeals(self) -> list[dict]:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, ts, group_id, user_id, word, action, appeal_text, appeal_ts, source"
            " FROM punishments WHERE appeal_status = 'pending' ORDER BY appeal_ts",
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "group_id": r[2], "user_id": r[3], "word": r[4],
                "action": r[5], "appeal_text": r[6], "appeal_ts": r[7], "source": r[8],
            }
            for r in rows
        ]

    # ------------------------------------------------------------ 博客知识库

    async def replace_blog_posts(self, posts: list[dict]) -> None:
        """全量替换文章索引；每篇保留自己的 fetched_at（无则记为当前时间）。"""
        default_ts = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = await self._ensure()
        await conn.execute("DELETE FROM blog_posts")
        await conn.executemany(
            "INSERT INTO blog_posts (url, title, summary, category, published, excerpt, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    p.get("url", ""), p.get("title", ""), p.get("summary", ""),
                    p.get("category", ""), p.get("published", ""), p.get("excerpt", ""),
                    p.get("fetched_at") or default_ts,
                )
                for p in posts
            ],
        )
        await conn.commit()

    async def list_blog_posts(self) -> list[dict]:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT url, title, summary, category, published, excerpt, fetched_at"
            " FROM blog_posts"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "url": r[0], "title": r[1], "summary": r[2], "category": r[3],
                "published": r[4], "excerpt": r[5], "fetched_at": r[6],
            }
            for r in rows
        ]

    async def count_blog_posts(self) -> int:
        conn = await self._ensure()
        async with conn.execute("SELECT COUNT(*) FROM blog_posts") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

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
