"""群友存下的表情包（文件在 data/stickers/，这里只存元数据与语义描述）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from src.store.base import StoreBase

_COLS = (
    "id", "sha", "file", "mime", "text", "meaning", "usage", "tags",
    "group_id", "saved_by", "source", "uses", "last_used", "created_at",
)


def _row(r) -> dict:
    return dict(zip(_COLS, r))


class StickersMixin(StoreBase):
    async def add_sticker(
        self, *, sha: str, file: str, mime: str, text: str, meaning: str, usage: str,
        tags: str, group_id: int, saved_by: int, source: str,
    ) -> int | None:
        """新增一张；同一内容（sha）已存在时返回 None。"""
        conn = await self._ensure()
        try:
            cur = await conn.execute(
                "INSERT INTO stickers (sha, file, mime, text, meaning, usage, tags, group_id,"
                " saved_by, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, file, mime, text, meaning, usage, tags, int(group_id), int(saved_by), source,
                 datetime.now().astimezone().isoformat(timespec="seconds")),
            )
        except sqlite3.IntegrityError:
            return None
        await conn.commit()
        return int(cur.lastrowid)

    async def get_sticker(self, sticker_id: int) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            f"SELECT {', '.join(_COLS)} FROM stickers WHERE id = ?", (int(sticker_id),)
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None

    async def get_sticker_by_sha(self, sha: str) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            f"SELECT {', '.join(_COLS)} FROM stickers WHERE sha = ?", (sha,)
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None

    async def list_stickers(self) -> list[dict]:
        """全部（库有上限，量不大），新存的在前。"""
        conn = await self._ensure()
        async with conn.execute(
            f"SELECT {', '.join(_COLS)} FROM stickers ORDER BY id DESC"
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def delete_sticker(self, sticker_id: int) -> dict | None:
        """删除并返回被删的记录（调用方负责删文件）；不存在返回 None。"""
        row = await self.get_sticker(sticker_id)
        if row is None:
            return None
        conn = await self._ensure()
        await conn.execute("DELETE FROM stickers WHERE id = ?", (int(sticker_id),))
        await conn.commit()
        return row

    async def bump_sticker_use(self, sticker_id: int) -> None:
        conn = await self._ensure()
        await conn.execute(
            "UPDATE stickers SET uses = uses + 1, last_used = ? WHERE id = ?",
            (datetime.now().astimezone().isoformat(timespec="seconds"), int(sticker_id)),
        )
        await conn.commit()

    async def count_stickers_saved(self, *, since: str, saved_by: int | None = None,
                                   source: str | None = None) -> int:
        """since（ISO 时间，含）以来存下的张数；可按存的人 / 来源过滤（每日上限用）。"""
        sql, args = "SELECT COUNT(*) FROM stickers WHERE created_at >= ?", [since]
        if saved_by is not None:
            sql += " AND saved_by = ?"
            args.append(int(saved_by))
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        conn = await self._ensure()
        async with conn.execute(sql, args) as cur:
            return int((await cur.fetchone())[0])
