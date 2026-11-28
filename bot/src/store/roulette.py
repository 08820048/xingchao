"""轮盘对局审计与结算。

积分余额/流水在 `points.py` 的通用账本里；这里只负责：
- 一局轮盘的原子结算（写 roulette_games + 更新 points_accounts + 写 points_ledger）
- 对局统计与最近对局查询
"""

from __future__ import annotations

import json
from datetime import datetime

from src.store.base import StoreBase


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RouletteMixin(StoreBase):
    async def settle_roulette(
        self,
        group_id: int,
        creator_id: int,
        victim_id: int,
        player_count: int,
        stake: int,
        bonus: int,
        deltas: dict[int, int],
    ) -> dict[int, int]:
        """一次性应用本局所有余额变化并记录对局/流水，返回受影响玩家的最新余额。

        deltas 由结算规则算出，所有值之和必须为 0；这里不再改写规则，
        只负责原子落库，避免「扣了甲没加乙」或「有对局没流水」的半结算状态。
        """
        if sum(deltas.values()) != 0:
            raise ValueError(f"轮盘结算不平：{deltas}")
        conn = await self._ensure()
        now = _now()
        note = f"庄家 {creator_id} / 中枪 {victim_id} / {player_count} 人"
        try:
            cur = await conn.execute(
                "INSERT INTO roulette_games (group_id, creator_id, victim_id, player_count,"
                " stake, bonus, deltas, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    group_id, creator_id, victim_id, player_count, stake, bonus,
                    json.dumps(deltas, ensure_ascii=False, sort_keys=True), now,
                ),
            )
            game_id = int(cur.lastrowid)
            for user_id, delta in deltas.items():
                if not delta:
                    continue
                await conn.execute(
                    "INSERT INTO points_accounts"
                    " (group_id, user_id, balance, created_at, updated_at)"
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
                    " VALUES (?, ?, ?, ?, 'roulette', ?, ?, ?)",
                    (group_id, user_id, int(delta), balances[user_id], str(game_id),
                     note[:200], now),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return balances

    async def roulette_stats(self, group_id: int, user_id: int) -> dict[str, int]:
        """该用户在本群的对局统计：总局数、当庄次数、庄家赢的次数、中枪次数。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(*),"
            " COALESCE(SUM(CASE WHEN creator_id = ? THEN 1 ELSE 0 END), 0),"
            " COALESCE(SUM(CASE WHEN creator_id = ? AND victim_id != ? THEN 1 ELSE 0 END), 0),"
            " COALESCE(SUM(CASE WHEN victim_id = ? THEN 1 ELSE 0 END), 0)"
            " FROM roulette_games WHERE group_id = ? AND (creator_id = ? OR victim_id = ?)",
            (user_id, user_id, user_id, user_id, group_id, user_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return {
            "games": int(row[0]) if row else 0,
            "as_creator": int(row[1]) if row else 0,
            "creator_wins": int(row[2]) if row else 0,
            "as_victim": int(row[3]) if row else 0,
        }

    async def recent_roulette_games(self, group_id: int, limit: int = 5) -> list[dict]:
        """最近几局流水，供 `/轮盘 记录` 或 `/积分 记录` 查看。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT creator_id, victim_id, player_count, stake, bonus, deltas, created_at"
            " FROM roulette_games WHERE group_id = ? ORDER BY id DESC LIMIT ?",
            (group_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for r in rows:
            try:
                deltas = json.loads(r[5])
            except (TypeError, json.JSONDecodeError):
                deltas = {}
            result.append({
                "creator_id": int(r[0]),
                "victim_id": int(r[1]),
                "player_count": int(r[2]),
                "stake": int(r[3]),
                "bonus": int(r[4]),
                "deltas": deltas,
                "created_at": r[6],
            })
        return result
