"""QQ 登录 / 掉线事件（bot_events）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.store.base import StoreBase


class EventsMixin(StoreBase):
    async def add_bot_event(
        self, event: str, detail: str = "", duration_seconds: int = 0,
        ts: str | None = None,
    ) -> int:
        """记录一次 QQ 登录/掉线事件。event: online / offline。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO bot_events (ts, event, detail, duration_seconds) VALUES (?, ?, ?, ?)",
            (
                ts or datetime.now().astimezone().isoformat(timespec="seconds"),
                str(event), str(detail)[:500], max(0, int(duration_seconds)),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_bot_events(
        self, limit: int = 50, days: int | None = None,
    ) -> list[dict]:
        """最近的登录/掉线事件，按时间倒序。"""
        conn = await self._ensure()
        sql = "SELECT id, ts, event, detail, duration_seconds FROM bot_events"
        params: list = []
        if days is not None:
            cutoff = (
                datetime.now().astimezone() - timedelta(days=max(1, int(days)))
            ).isoformat(timespec="seconds")
            sql += " WHERE ts >= ?"
            params.append(cutoff)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 5000)))
        async with conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": int(r[0]), "ts": r[1], "event": r[2],
                "detail": r[3] or "", "duration_seconds": int(r[4] or 0),
            }
            for r in rows
        ]

    async def last_bot_event(self, event: str | None = None) -> dict | None:
        conn = await self._ensure()
        sql = "SELECT id, ts, event, detail, duration_seconds FROM bot_events"
        params: tuple = ()
        if event is not None:
            sql += " WHERE event = ?"
            params = (str(event),)
        sql += " ORDER BY id DESC LIMIT 1"
        async with conn.execute(sql, params) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        return {
            "id": int(r[0]), "ts": r[1], "event": r[2],
            "detail": r[3] or "", "duration_seconds": int(r[4] or 0),
        }
