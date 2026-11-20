"""博客文章索引与群知识库。"""

from __future__ import annotations

from datetime import datetime

from src.store.base import StoreBase


class ContentMixin(StoreBase):
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

    # ------------------------------------------------------------ 个人提醒

    async def add_kb_entry(
        self, group_id: int, question: str, answer: str, keywords: str = "",
        source: str = "manual", created_by: int = 0,
    ) -> int:
        """新增一条群知识（FAQ / 群规）。question 与 answer 都必填。"""
        conn = await self._ensure()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        cur = await conn.execute(
            "INSERT INTO kb_entries (group_id, question, answer, keywords, hits, source,"
            " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (group_id, question[:200], answer[:2000], keywords[:300], source, created_by, now, now),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_kb_entries(self, group_id: int | None = None) -> list[dict]:
        conn = await self._ensure()
        sql = ("SELECT id, group_id, question, answer, keywords, hits, source, created_by,"
               " created_at, updated_at FROM kb_entries")
        params: tuple = ()
        if group_id is not None:
            sql += " WHERE group_id = ?"
            params = (group_id,)
        sql += " ORDER BY hits DESC, id ASC"
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "group_id": r[1], "question": r[2], "answer": r[3], "keywords": r[4],
             "hits": r[5], "source": r[6], "created_by": r[7], "created_at": r[8], "updated_at": r[9]}
            for r in rows
        ]

    async def get_kb_entry(self, entry_id: int) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, group_id, question, answer, keywords, hits, source, created_by,"
            " created_at, updated_at FROM kb_entries WHERE id = ?", (entry_id,),
        ) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        return {"id": r[0], "group_id": r[1], "question": r[2], "answer": r[3], "keywords": r[4],
                "hits": r[5], "source": r[6], "created_by": r[7], "created_at": r[8], "updated_at": r[9]}

    async def update_kb_entry(self, entry_id: int, **fields) -> bool:
        allowed = {"question", "answer", "keywords"}
        sets, vals = [], []
        for key in allowed & set(fields):
            value = fields[key]
            limit = {"question": 200, "answer": 2000, "keywords": 300}[key]
            sets.append(f"{key} = ?")
            vals.append(str(value)[:limit])
        if not sets:
            return False
        sets.append("updated_at = ?")
        vals.append(datetime.now().astimezone().isoformat(timespec="seconds"))
        vals.append(entry_id)
        conn = await self._ensure()
        cur = await conn.execute(f"UPDATE kb_entries SET {', '.join(sets)} WHERE id = ?", tuple(vals))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def delete_kb_entry(self, entry_id: int) -> bool:
        conn = await self._ensure()
        cur = await conn.execute("DELETE FROM kb_entries WHERE id = ?", (entry_id,))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def bump_kb_hits(self, entry_id: int) -> None:
        conn = await self._ensure()
        await conn.execute("UPDATE kb_entries SET hits = hits + 1 WHERE id = ?", (entry_id,))
        await conn.commit()

    # ---------------------------------------------------------------- 动作计数（工作台账）
