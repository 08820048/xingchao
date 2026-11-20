"""发言计数（msg_stat / msg_stat_user）与工作台账计数（action_stat）。"""

from __future__ import annotations

from datetime import datetime

from src.store.base import StoreBase


class StatsMixin(StoreBase):
    async def incr_msg_stats(self, group_id: int, day: str, user_id: int) -> None:
        """群级 + 用户级发言计数，一次事务一次提交（每条群消息都会调用，是最热的写路径）。"""
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO msg_stat (group_id, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(group_id, day) DO UPDATE SET count = count + 1",
            (group_id, day),
        )
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

    async def get_activity_since(self, group_id: int, since_day: str) -> dict[int, int]:
        """某群自 since_day（YYYY-MM-DD，含）起各成员的发言总数。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, SUM(count) FROM msg_stat_user WHERE group_id = ? AND day >= ?"
            " GROUP BY user_id",
            (group_id, since_day),
        ) as cur:
            return {int(r[0]): int(r[1]) for r in await cur.fetchall()}

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

    async def incr_action(self, group_id: int, action: str, day: str | None = None) -> None:
        """记一次「机器人为群做了什么」（只计数，不含任何内容）。

        action 用统一的小写标识，如 kb_answer / recall_query / ai_reply /
        ad_blocked / link_preview / essence_set / join_approved。
        """
        day = day or datetime.now().astimezone().strftime("%Y-%m-%d")
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO action_stat (day, group_id, action, count) VALUES (?, ?, ?, 1)"
            " ON CONFLICT(day, group_id, action) DO UPDATE SET count = count + 1",
            (day, int(group_id), str(action)[:40]),
        )
        await conn.commit()

    async def action_totals(
        self, start: str, end: str, group_id: int | None = None,
    ) -> dict[str, int]:
        """[start, end] 闭区间内各动作的次数合计。"""
        conn = await self._ensure()
        sql = "SELECT action, SUM(count) FROM action_stat WHERE day >= ? AND day <= ?"
        params: list = [start, end]
        if group_id is not None:
            sql += " AND group_id = ?"
            params.append(int(group_id))
        sql += " GROUP BY action"
        async with conn.execute(sql, tuple(params)) as cur:
            return {str(r[0]): int(r[1]) for r in await cur.fetchall()}

    async def action_by_group(self, start: str, end: str) -> dict[int, dict[str, int]]:
        """同一区间内按群分组的动作次数。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT group_id, action, SUM(count) FROM action_stat"
            " WHERE day >= ? AND day <= ? GROUP BY group_id, action",
            (start, end),
        ) as cur:
            out: dict[int, dict[str, int]] = {}
            for gid, action, count in await cur.fetchall():
                out.setdefault(int(gid), {})[str(action)] = int(count)
            return out
