"""处罚记录、申诉、敏感词命中统计。"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.store.base import StoreBase


class ModerationMixin(StoreBase):
    async def add_punishment(
        self,
        group_id: int,
        user_id: int,
        word: str,
        action: str,
        mute_minutes: int = 0,
        reason: str = "",
        source: str = "sensitive",
        counted: bool = True,
        message_id: int | None = None,
    ) -> int:
        """记录一次处罚（不存聊天内容，只存命中词与处理结果；message_id 仅用于申诉时回读日志）。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO punishments (ts, group_id, user_id, word, action, mute_minutes,"
            " reason, source, counted, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                group_id, user_id, word, action, mute_minutes, reason[:200], source,
                1 if counted else 0, message_id,
            ),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def count_strikes(
        self, group_id: int, user_id: int, days: int = 30, source: str = "sensitive"
    ) -> int:
        """窗口期内该用户在该群已计入升级的违规次数（不含未计入与申诉通过的记录）。"""
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(*) FROM punishments WHERE group_id = ? AND user_id = ?"
            " AND source = ? AND ts >= ? AND counted = 1 AND appeal_status != 'accepted'",
            (group_id, user_id, source, since),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def incr_word_stat(self, word: str, outcome: str) -> None:
        """敏感词命中统计。outcome：released=AI 复核放行 / punished=确认违规 / fallback=复核不可用兜底。"""
        if outcome not in ("released", "punished", "fallback"):
            return
        conn = await self._ensure()
        await conn.execute(
            f"INSERT INTO word_stat (word, candidates, {outcome}, last_ts) VALUES (?, 1, 1, ?)"
            f" ON CONFLICT(word) DO UPDATE SET candidates = candidates + 1,"
            f" {outcome} = {outcome} + 1, last_ts = excluded.last_ts",
            (word, datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        await conn.commit()

    async def word_stats(self) -> list[dict]:
        """每个词的命中情况 + 申诉通过次数（来自违规记录）。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT w.word, w.candidates, w.released, w.punished, w.fallback, w.last_ts,"
            " (SELECT COUNT(*) FROM punishments p WHERE p.word = w.word"
            "  AND p.source = 'sensitive' AND p.appeal_status = 'accepted'),"
            " (SELECT COUNT(*) FROM punishments p WHERE p.word = w.word"
            "  AND p.source = 'sensitive' AND p.appeal_status != 'none')"
            " FROM word_stat w ORDER BY w.candidates DESC, w.word"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "word": r[0], "candidates": r[1], "released": r[2], "punished": r[3],
                "fallback": r[4], "last_ts": r[5], "appeal_accepted": r[6], "appeals": r[7],
            }
            for r in rows
        ]

    async def count_actions_today(self, group_id: int, action: str, source: str = "sensitive") -> int:
        """某群今日（本地时区自然日）某类处罚已执行的次数，如 kick。"""
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(*) FROM punishments WHERE group_id = ? AND action = ? AND source = ?"
            " AND ts >= ?",
            (group_id, action, source, midnight.isoformat(timespec="seconds")),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_punishments(
        self, limit: int = 100, group_id: int | None = None,
        start: str | None = None, end: str | None = None,
    ) -> list[dict]:
        """处罚记录（默认最近 limit 条）。

        start/end 为 YYYY-MM-DD 闭区间，用于工作台账按周汇总。
        """
        conn = await self._ensure()
        sql = (
            "SELECT id, ts, group_id, user_id, word, action, mute_minutes, reason,"
            " appeal_status, appeal_text, source, counted FROM punishments WHERE 1=1"
        )
        params: list = []
        if group_id is not None:
            sql += " AND group_id = ?"
            params.append(group_id)
        if start:
            sql += " AND substr(ts, 1, 10) >= ?"
            params.append(start)
        if end:
            sql += " AND substr(ts, 1, 10) <= ?"
            params.append(end)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "group_id": r[2], "user_id": r[3], "word": r[4],
                "action": r[5], "mute_minutes": r[6], "reason": r[7],
                "appeal_status": r[8], "appeal_text": r[9], "source": r[10],
                "counted": bool(r[11]),
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
            "SELECT id, ts, group_id, word, action, appeal_status, message_id, mute_minutes,"
            " source FROM punishments WHERE user_id = ? AND ts >= ? ORDER BY id DESC LIMIT 1",
            (user_id, since),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "ts": row[1], "group_id": row[2], "word": row[3],
            "action": row[4], "appeal_status": row[5], "message_id": row[6],
            "mute_minutes": row[7], "source": row[8],
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

    async def resolve_appeal(
        self, punishment_id: int, accept: bool, note: str = "", operator: str = ""
    ) -> dict | None:
        """处理申诉，返回该记录（含群/用户，便于撤销禁言）；非待处理记录返回 None。"""
        conn = await self._ensure()
        status = "accepted" if accept else "rejected"
        cur = await conn.execute(
            "UPDATE punishments SET appeal_status = ?, appeal_note = ?, resolved_by = ?"
            " WHERE id = ? AND appeal_status = 'pending'",
            (status, note[:200], operator[:40], punishment_id),
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

    async def get_punishment(self, punishment_id: int) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, ts, group_id, user_id, word, action, mute_minutes, reason, source,"
            " appeal_status, appeal_text, appeal_ts, message_id, appeal_note, resolved_by"
            " FROM punishments WHERE id = ?",
            (punishment_id,),
        ) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        keys = ("id", "ts", "group_id", "user_id", "word", "action", "mute_minutes", "reason",
                "source", "appeal_status", "appeal_text", "appeal_ts", "message_id",
                "appeal_note", "resolved_by")
        return dict(zip(keys, r, strict=True))

    async def mark_appeal_reminded(self, punishment_id: int) -> None:
        conn = await self._ensure()
        await conn.execute(
            "UPDATE punishments SET appeal_reminded = 1 WHERE id = ?", (punishment_id,)
        )
        await conn.commit()

    async def pending_appeals(self) -> list[dict]:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, ts, group_id, user_id, word, action, appeal_text, appeal_ts, source,"
            " appeal_reminded FROM punishments WHERE appeal_status = 'pending' ORDER BY appeal_ts",
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "group_id": r[2], "user_id": r[3], "word": r[4],
                "action": r[5], "appeal_text": r[6], "appeal_ts": r[7], "source": r[8],
                "reminded": bool(r[9]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------ 博客知识库
