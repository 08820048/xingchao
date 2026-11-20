"""定时消息、订阅推送、个人提醒。"""

from __future__ import annotations

from datetime import datetime

from src.store.base import StoreBase


class SchedulesMixin(StoreBase):
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

    async def add_feed_sub(self, group_id: int, source: str, time_: str, repeat: str,
                           weekday: int | None, params: str, ai_summary: bool) -> int:
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO feed_subs (group_id, source, time, repeat, weekday, params, ai_summary,"
            " enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (group_id, source, time_, repeat, weekday, params, int(ai_summary),
             datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_feed_subs(self, enabled_only: bool = False) -> list[dict]:
        conn = await self._ensure()
        sql = ("SELECT id, group_id, source, time, repeat, weekday, params, ai_summary, enabled"
               " FROM feed_subs")
        if enabled_only:
            sql += " WHERE enabled = 1"
        async with conn.execute(sql + " ORDER BY id") as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "group_id": r[1], "source": r[2], "time": r[3], "repeat": r[4],
             "weekday": r[5], "params": r[6], "ai_summary": bool(r[7]), "enabled": bool(r[8])}
            for r in rows
        ]

    async def update_feed_sub(self, sub_id: int, **fields) -> None:
        allowed = {"group_id", "time", "repeat", "weekday", "params", "ai_summary", "enabled"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(int(v) if isinstance(v, bool) else v)
        if not sets:
            return
        vals.append(sub_id)
        conn = await self._ensure()
        await conn.execute(f"UPDATE feed_subs SET {', '.join(sets)} WHERE id = ?", vals)
        await conn.commit()

    async def delete_feed_sub(self, sub_id: int) -> bool:
        conn = await self._ensure()
        cur = await conn.execute("DELETE FROM feed_subs WHERE id = ?", (sub_id,))
        await conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ 长期记忆（成员印象 / 群记忆）
    # scope='user'：某群里某成员的印象；scope='group'：某群的群记忆（user_id=0）。
    # 只存模型提炼的短事实，不存聊天原话；成员可随时清除并退出（memory_optout）。

    async def add_reminder(self, user_id: int, group_id: int | None, due_ts: int, content: str) -> int:
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT INTO reminders (user_id, group_id, due_ts, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, group_id, int(due_ts), content,
             datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        await conn.commit()
        return int(cur.lastrowid)

    async def list_reminders(self, user_id: int | None = None) -> list[dict]:
        conn = await self._ensure()
        sql = "SELECT id, user_id, group_id, due_ts, content FROM reminders"
        params: tuple = ()
        if user_id is not None:
            sql += " WHERE user_id = ?"
            params = (user_id,)
        async with conn.execute(sql + " ORDER BY due_ts", params) as cur:
            rows = await cur.fetchall()
        return [{"id": r[0], "user_id": r[1], "group_id": r[2], "due_ts": r[3], "content": r[4]} for r in rows]

    async def due_reminders(self, now_ts: int) -> list[dict]:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT id, user_id, group_id, due_ts, content FROM reminders WHERE due_ts <= ? ORDER BY due_ts",
            (int(now_ts),),
        ) as cur:
            rows = await cur.fetchall()
        return [{"id": r[0], "user_id": r[1], "group_id": r[2], "due_ts": r[3], "content": r[4]} for r in rows]

    async def delete_reminder(self, reminder_id: int, user_id: int | None = None) -> bool:
        conn = await self._ensure()
        sql, params = "DELETE FROM reminders WHERE id = ?", [reminder_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------- 群知识库
