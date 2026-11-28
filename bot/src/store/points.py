"""按群隔离的通用积分账本：账户、只追加流水、每日幂等奖励。

设计约定（和 roulette 的旧筹码账本的区别）：
- `points_accounts.balance` 是读缓存，真正的审计真相是 `points_ledger`；
  任何加减分都必须写一条流水，禁止只改余额。
- 每日类奖励先写 `points_daily` 唯一键，再改余额；并发/重复调用只会成功一次。
- 所有金额都是整数，按群隔离，不跨群转移。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.store.base import StoreBase


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class PointsMixin(StoreBase):
    async def get_points(self, group_id: int, user_id: int) -> int | None:
        """本群积分余额；没有账户时返回 None。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def ensure_points(self, group_id: int, user_id: int, initial: int) -> int:
        """没有账户就开户；开户赠送额写一条 initial 流水，返回当前余额。"""
        conn = await self._ensure()
        now = _now()
        cur = await conn.execute(
            "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id, user_id) DO NOTHING",
            (group_id, user_id, int(initial), now, now),
        )
        if cur.rowcount and initial:
            await conn.execute(
                "INSERT INTO points_ledger"
                " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                " VALUES (?, ?, ?, ?, 'initial', NULL, '首次开户', ?)",
                (group_id, user_id, int(initial), int(initial), now),
            )
        await conn.commit()
        async with conn.execute(
            "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else int(initial)

    async def add_points(
        self,
        group_id: int,
        user_id: int,
        delta: int,
        reason: str,
        ref: str | None = None,
        note: str = "",
    ) -> int:
        """加/减分并写流水，返回最新余额。delta=0 时只查余额。"""
        conn = await self._ensure()
        if delta:
            try:
                await conn.execute(
                    "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                    " balance = points_accounts.balance + excluded.balance,"
                    " updated_at = excluded.updated_at",
                    (group_id, user_id, int(delta), _now(), _now()),
                )
                async with conn.execute(
                    "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                ) as cur:
                    row = await cur.fetchone()
                balance = int(row[0]) if row else 0
                await conn.execute(
                    "INSERT INTO points_ledger"
                    " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (group_id, user_id, int(delta), balance, reason, ref, note[:200], _now()),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            return balance
        async with conn.execute(
            "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_points_batch(
        self,
        group_id: int,
        deltas: dict[int, int],
        reason: str,
        ref: str | None = None,
        note: str = "",
    ) -> dict[int, int]:
        """一次事务里应用多个账户的加减分，并为每个非零变化写流水。

        调用方负责保证业务上「该扣的扣了、该加的加了」；这里只保证落库原子性。
        """
        if not deltas:
            return {}
        conn = await self._ensure()
        now = _now()
        try:
            for user_id, delta in deltas.items():
                if not delta:
                    continue
                await conn.execute(
                    "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                    " balance = points_accounts.balance + excluded.balance,"
                    " updated_at = excluded.updated_at",
                    (group_id, user_id, int(delta), now, now),
                )
            balances: dict[int, int] = {}
            for user_id in deltas:
                async with conn.execute(
                    "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                ) as cur:
                    row = await cur.fetchone()
                balances[user_id] = int(row[0]) if row else 0
            for user_id, delta in deltas.items():
                if not delta:
                    continue
                await conn.execute(
                    "INSERT INTO points_ledger"
                    " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (group_id, user_id, int(delta), balances[user_id], reason, ref,
                     note[:200], now),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return balances

    async def top_points(self, group_id: int, limit: int = 10) -> list[tuple[int, int]]:
        """本群积分排行 [(user_id, balance)]。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, balance FROM points_accounts WHERE group_id = ?"
            " ORDER BY balance DESC, user_id LIMIT ?",
            (group_id, limit),
        ) as cur:
            return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    async def points_history(
        self, group_id: int, user_id: int, limit: int = 10
    ) -> list[dict]:
        """个人积分流水（倒序）。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT delta, balance_after, reason, ref, note, created_at"
            " FROM points_ledger WHERE group_id = ? AND user_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (group_id, user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "delta": int(r[0]),
                "balance_after": int(r[1]),
                "reason": r[2],
                "ref": r[3],
                "note": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def daily_claimed(self, group_id: int, user_id: int, day: str, kind: str) -> bool:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT 1 FROM points_daily"
            " WHERE group_id = ? AND user_id = ? AND day = ? AND kind = ?",
            (group_id, user_id, day, kind),
        ) as cur:
            return await cur.fetchone() is not None

    async def claim_daily(
        self,
        group_id: int,
        user_id: int,
        day: str,
        kind: str,
        amount: int,
        note: str = "",
        threshold: int | None = None,
    ) -> tuple[bool, int, str]:
        """每日幂等奖励：同一天同 kind 只成功一次。

        threshold 不为空时，余额已达到阈值则不发（用于破产补助）。
        返回 (是否发放, 最新余额, 说明)。
        """
        conn = await self._ensure()
        async with conn.execute(
            "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        balance = int(row[0]) if row else 0
        if threshold is not None and balance >= threshold:
            return False, balance, f"你现在有 {balance} 积分，还没到需要补助的程度"
        now = _now()
        try:
            cur = await conn.execute(
                "INSERT INTO points_daily (group_id, user_id, day, kind, amount, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(group_id, user_id, day, kind) DO NOTHING",
                (group_id, user_id, day, kind, int(amount), now),
            )
            if cur.rowcount == 0:
                await conn.commit()
                return False, balance, "今天已经领过了，明天再来"
            if amount:
                await conn.execute(
                    "INSERT INTO points_accounts"
                    " (group_id, user_id, balance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                    " balance = points_accounts.balance + excluded.balance,"
                    " updated_at = excluded.updated_at",
                    (group_id, user_id, int(amount), now, now),
                )
            async with conn.execute(
                "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            new_balance = int(row[0]) if row else 0
            if amount:
                await conn.execute(
                    "INSERT INTO points_ledger"
                    " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                    " VALUES (?, ?, ?, ?, 'daily', ?, ?, ?)",
                    (group_id, user_id, int(amount), new_balance, f"{kind}:{day}",
                     note[:200], now),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return True, new_balance, f"领到 {amount} 积分，当前 {new_balance}"

    async def sign_streak(self, group_id: int, user_id: int, day: str) -> int:
        """截至 day 的前一天，连续签到天数（用于计算今天签到奖励）。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT day FROM points_daily"
            " WHERE group_id = ? AND user_id = ? AND kind = 'sign' AND day < ?"
            " ORDER BY day DESC LIMIT 400",
            (group_id, user_id, day),
        ) as cur:
            rows = await cur.fetchall()
        expected = date.fromisoformat(day) - timedelta(days=1)
        streak = 0
        for (raw,) in rows:
            try:
                current = date.fromisoformat(str(raw))
            except ValueError:
                continue
            if current == expected:
                streak += 1
                expected -= timedelta(days=1)
            elif current < expected:
                break
        return streak

    async def points_overview(self, group_id: int, day: str) -> dict[str, int]:
        """面板概览：账户数、总积分、今日签到人数、流水条数。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM points_accounts"
            " WHERE group_id = ?",
            (group_id,),
        ) as cur:
            accounts, total = await cur.fetchone()
        async with conn.execute(
            "SELECT COUNT(*) FROM points_daily"
            " WHERE group_id = ? AND day = ? AND kind = 'sign'",
            (group_id, day),
        ) as cur:
            sign_today = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT COUNT(*) FROM points_ledger WHERE group_id = ?", (group_id,)
        ) as cur:
            ledger = (await cur.fetchone())[0]
        return {
            "accounts": int(accounts or 0),
            "total": int(total or 0),
            "sign_today": int(sign_today or 0),
            "ledger": int(ledger or 0),
        }

    async def points_accounts(
        self, group_id: int, limit: int = 200
    ) -> list[dict]:
        """面板账户列表，按余额降序。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, balance, updated_at FROM points_accounts"
            " WHERE group_id = ? ORDER BY balance DESC, user_id LIMIT ?",
            (group_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"user_id": int(r[0]), "balance": int(r[1]), "updated_at": r[2]}
            for r in rows
        ]

    async def recent_points_ledger(self, group_id: int, limit: int = 50) -> list[dict]:
        """面板用：本群最近的积分流水（跨用户）。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, delta, balance_after, reason, ref, note, created_at"
            " FROM points_ledger WHERE group_id = ? ORDER BY id DESC LIMIT ?",
            (group_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "user_id": int(r[0]),
                "delta": int(r[1]),
                "balance_after": int(r[2]),
                "reason": r[3],
                "ref": r[4],
                "note": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    async def transfer_points(
        self,
        group_id: int,
        from_user: int,
        to_user: int,
        amount: int,
        fee: int,
    ) -> tuple[bool, int, int, str]:
        """群内转账：扣 from（金额+手续费），加 to（金额），手续费销毁。

        返回 (是否成功, from 最新余额, to 最新余额, 说明)。
        余额检查、双方更新、两条流水在同一事务里完成。
        """
        if from_user == to_user:
            return False, 0, 0, "不能转给自己"
        if amount <= 0:
            return False, 0, 0, "转账金额必须大于 0"
        if fee < 0:
            return False, 0, 0, "手续费不能为负"
        total = amount + fee
        conn = await self._ensure()
        now = _now()
        try:
            async with conn.execute(
                "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                (group_id, from_user),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return False, 0, 0, "你还没有积分账户"
            from_balance = int(row[0])
            # 条件更新：余额检查与扣款在同一条 SQL 里，避免两个并发转账都通过检查
            cur = await conn.execute(
                "UPDATE points_accounts SET balance = balance - ?, updated_at = ?"
                " WHERE group_id = ? AND user_id = ? AND balance >= ?",
                (total, now, group_id, from_user, total),
            )
            if cur.rowcount == 0:
                return False, from_balance, 0, (
                    f"积分不够：需要 {total}（转账 {amount} + 手续费 {fee}），当前 {from_balance}"
                )
            await conn.execute(
                "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                " balance = points_accounts.balance + excluded.balance,"
                " updated_at = excluded.updated_at",
                (group_id, to_user, amount, now, now),
            )
            async with conn.execute(
                "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                (group_id, from_user),
            ) as cur:
                from_balance = int((await cur.fetchone())[0])
            async with conn.execute(
                "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                (group_id, to_user),
            ) as cur:
                to_balance = int((await cur.fetchone())[0])
            await conn.execute(
                "INSERT INTO points_ledger"
                " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                " VALUES (?, ?, ?, ?, 'transfer', NULL, ?, ?)",
                (group_id, from_user, -total, from_balance,
                 f"转给 {to_user}，手续费 {fee}"[:200], now),
            )
            await conn.execute(
                "INSERT INTO points_ledger"
                " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                " VALUES (?, ?, ?, ?, 'transfer', NULL, ?, ?)",
                (group_id, to_user, amount, to_balance,
                 f"来自 {from_user}"[:200], now),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return True, from_balance, to_balance, (
            f"已转给 {to_user} {amount} 积分（手续费 {fee}），"
            f"你当前 {from_balance}；对方 {to_balance}"
        )
