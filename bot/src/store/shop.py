"""按群积分商店：商品、购买、兑换记录、人工处理与退款。

- 商品按群隔离；stock=-1 表示不限量，per_user_limit=0 表示不限购
- 购买是一个事务：扣积分 + 扣库存 + 写兑换单 + 写积分流水
- 拒绝兑换时退款并恢复库存；完成兑换不再改积分
- 购买/处理的人性化提示在 plugins/shop.py；这里只保证数据一致
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from src.store.base import StoreBase

DELIVERY = ("manual", "title", "announcement")
STATUS = ("pending", "fulfilled", "rejected")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row(r) -> dict:
    return {
        "id": int(r[0]),
        "group_id": int(r[1]),
        "name": r[2],
        "description": r[3] or "",
        "price": int(r[4]),
        "stock": int(r[5]),
        "per_user_limit": int(r[6]),
        "enabled": bool(r[7]),
        "delivery": r[8],
        "delivery_data": r[9] or "",
        "created_at": r[10],
        "updated_at": r[11],
    }


_ITEM_COLUMNS = (
    "id, group_id, name, description, price, stock, per_user_limit, enabled,"
    " delivery, delivery_data, created_at, updated_at"
)


class ShopMixin(StoreBase):
    def _group_lock(self, group_id: int) -> asyncio.Lock:
        locks = self.__dict__.setdefault("_shop_locks", {})
        return locks.setdefault(group_id, asyncio.Lock())

    async def shop_items(self, group_id: int, include_disabled: bool = False) -> list[dict]:
        conn = await self._ensure()
        where = "group_id = ?" + ("" if include_disabled else " AND enabled = 1")
        async with conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM shop_items WHERE {where} ORDER BY id",
            (group_id,),
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def shop_item(self, group_id: int, item_id: int) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM shop_items WHERE id = ? AND group_id = ?",
            (item_id, group_id),
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def add_shop_item(
        self,
        group_id: int,
        name: str,
        price: int,
        description: str = "",
        stock: int = -1,
        per_user_limit: int = 0,
        delivery: str = "manual",
        delivery_data: str = "",
    ) -> tuple[bool, int, str]:
        async with self._group_lock(group_id):
            return await self._add_shop_item_locked(
                group_id, name, price, description, stock, per_user_limit,
                delivery, delivery_data,
            )

    async def update_shop_item(
        self, group_id: int, item_id: int, fields: dict
    ) -> tuple[bool, str]:
        async with self._group_lock(group_id):
            return await self._update_shop_item_locked(group_id, item_id, fields)

    async def buy_shop_item(
        self, group_id: int, user_id: int, item_id: int, user_note: str = ""
    ) -> tuple[bool, int, str]:
        async with self._group_lock(group_id):
            return await self._buy_shop_item_locked(
                group_id, user_id, item_id, user_note
            )

    async def resolve_shop_redemption(
        self,
        group_id: int,
        redemption_id: int,
        resolver: str,
        accept: bool,
        note: str = "",
    ) -> tuple[bool, str]:
        async with self._group_lock(group_id):
            return await self._resolve_shop_redemption_locked(
                group_id, redemption_id, resolver, accept, note
            )

    async def _add_shop_item_locked(
        self,
        group_id: int,
        name: str,
        price: int,
        description: str = "",
        stock: int = -1,
        per_user_limit: int = 0,
        delivery: str = "manual",
        delivery_data: str = "",
    ) -> tuple[bool, int, str]:
        name = " ".join(str(name).split())[:40]
        description = " ".join(str(description).split())[:200]
        delivery = str(delivery or "manual").strip().lower()
        if not name:
            return False, 0, "商品名称不能为空"
        if not isinstance(price, int) or price < 1:
            return False, 0, "价格必须是正整数"
        if not isinstance(stock, int) or stock < -1:
            return False, 0, "库存必须是 -1（不限量）或非负整数"
        if not isinstance(per_user_limit, int) or per_user_limit < 0:
            return False, 0, "限购必须是非负整数（0=不限购）"
        if delivery not in DELIVERY:
            return False, 0, "交付方式只能是 manual / title / announcement"
        conn = await self._ensure()
        now = _now()
        cur = await conn.execute(
            "INSERT INTO shop_items (group_id, name, description, price, stock,"
            " per_user_limit, enabled, delivery, delivery_data, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (group_id, name, description, price, stock, per_user_limit, delivery,
             str(delivery_data or "")[:200], now, now),
        )
        await conn.commit()
        return True, int(cur.lastrowid), f"已上架 #{cur.lastrowid} {name}（{price} 积分）"

    async def _update_shop_item_locked(
        self, group_id: int, item_id: int, fields: dict
    ) -> tuple[bool, str]:
        allowed = {
            "name", "description", "price", "stock", "per_user_limit",
            "enabled", "delivery", "delivery_data",
        }
        updates: dict[str, object] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("name", "description", "delivery_data"):
                updates[key] = " ".join(str(value).split())[: (40 if key == "name" else 200)]
            elif key == "delivery":
                delivery = str(value or "").strip().lower()
                if delivery not in DELIVERY:
                    return False, "交付方式只能是 manual / title / announcement"
                updates[key] = delivery
            elif key == "enabled":
                updates[key] = 1 if value else 0
            else:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    return False, f"{key} 必须是整数"
                if key == "price" and number < 1:
                    return False, "价格必须是正整数"
                if key == "stock" and number < -1:
                    return False, "库存必须是 -1（不限量）或非负整数"
                if key == "per_user_limit" and number < 0:
                    return False, "限购必须是非负整数（0=不限购）"
                updates[key] = number
        if not updates:
            return False, "没有可更新的字段"
        conn = await self._ensure()
        updates["updated_at"] = _now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        cur = await conn.execute(
            f"UPDATE shop_items SET {sets} WHERE id = ? AND group_id = ?",
            (*updates.values(), item_id, group_id),
        )
        await conn.commit()
        return (cur.rowcount > 0), "已更新" if cur.rowcount else "商品不存在"

    async def _buy_shop_item_locked(
        self, group_id: int, user_id: int, item_id: int, user_note: str = ""
    ) -> tuple[bool, int, str]:
        """购买商品：扣积分、扣库存、写兑换单与流水，返回 (ok, 兑换单号, 说明)。

        并发说明：同一商品的限购检查不是数据库唯一约束，调用方（插件）用群锁串行化购买；
        余额和库存各自用带条件的 UPDATE，至少不会扣成负数或超卖。
        """
        item = await self.shop_item(group_id, item_id)
        if item is None or not item["enabled"]:
            return False, 0, "商品不存在或已下架"
        price = item["price"]
        if item["per_user_limit"]:
            conn = await self._ensure()
            async with conn.execute(
                "SELECT COUNT(*) FROM shop_redemptions"
                " WHERE group_id = ? AND user_id = ? AND item_id = ? AND status != 'rejected'",
                (group_id, user_id, item_id),
            ) as cur:
                bought = int((await cur.fetchone())[0])
            if bought >= item["per_user_limit"]:
                return False, 0, f"这件商品每人限购 {item['per_user_limit']} 次，你已经买过了"
        conn = await self._ensure()
        now = _now()
        try:
            cur = await conn.execute(
                "UPDATE points_accounts SET balance = balance - ?, updated_at = ?"
                " WHERE group_id = ? AND user_id = ? AND balance >= ?",
                (price, now, group_id, user_id, price),
            )
            if cur.rowcount == 0:
                await conn.rollback()
                return False, 0, f"积分不够：这件商品需要 {price}"
            if item["stock"] != -1:
                cur = await conn.execute(
                    "UPDATE shop_items SET stock = stock - 1, updated_at = ?"
                    " WHERE id = ? AND group_id = ? AND enabled = 1 AND stock > 0",
                    (now, item_id, group_id),
                )
                if cur.rowcount == 0:
                    await conn.rollback()
                    return False, 0, "库存不足"
            cur = await conn.execute(
                "INSERT INTO shop_redemptions (group_id, user_id, item_id, item_name,"
                " price, status, user_note, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (group_id, user_id, item_id, item["name"], price,
                 str(user_note or "")[:200], now),
            )
            redemption_id = int(cur.lastrowid)
            async with conn.execute(
                "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ) as cur:
                balance = int((await cur.fetchone())[0])
            await conn.execute(
                "INSERT INTO points_ledger"
                " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                " VALUES (?, ?, ?, ?, 'shop', ?, ?, ?)",
                (group_id, user_id, -price, balance, str(redemption_id),
                 f"购买 {item['name']}"[:200], now),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return True, redemption_id, f"已购买 {item['name']}，扣 {price} 积分，当前 {balance}"

    async def _resolve_shop_redemption_locked(
        self,
        group_id: int,
        redemption_id: int,
        resolver: str,
        accept: bool,
        note: str = "",
    ) -> tuple[bool, str]:
        """完成/拒绝兑换；拒绝时退款并恢复库存。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT user_id, item_id, item_name, price, status FROM shop_redemptions"
            " WHERE id = ? AND group_id = ?",
            (redemption_id, group_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False, "兑换单不存在"
        user_id, item_id, item_name, price, status = row
        if status != "pending":
            return False, f"这张兑换单已经是「{status}」，不能重复处理"
        now = _now()
        try:
            cur = await conn.execute(
                "UPDATE shop_redemptions SET status = ?, resolved_by = ?,"
                " resolved_at = ?, resolve_note = ?"
                " WHERE id = ? AND group_id = ? AND status = 'pending'",
                ("fulfilled" if accept else "rejected", str(resolver)[:60], now,
                 str(note or "")[:200], redemption_id, group_id),
            )
            if cur.rowcount == 0:
                await conn.rollback()
                return False, "这张兑换单已经被其他人处理了"
            if not accept:
                await conn.execute(
                    "INSERT INTO points_accounts (group_id, user_id, balance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                    " balance = points_accounts.balance + excluded.balance,"
                    " updated_at = excluded.updated_at",
                    (group_id, user_id, int(price), now, now),
                )
                async with conn.execute(
                    "SELECT balance FROM points_accounts WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                ) as cur:
                    balance = int((await cur.fetchone())[0])
                await conn.execute(
                    "INSERT INTO points_ledger"
                    " (group_id, user_id, delta, balance_after, reason, ref, note, created_at)"
                    " VALUES (?, ?, ?, ?, 'shop_refund', ?, ?, ?)",
                    (group_id, user_id, int(price), balance, str(redemption_id),
                     f"退款：{item_name}"[:200], now),
                )
                item = await self.shop_item(group_id, int(item_id))
                if item and item["stock"] != -1:
                    await conn.execute(
                        "UPDATE shop_items SET stock = stock + 1, updated_at = ?"
                        " WHERE id = ? AND group_id = ?",
                        (now, item_id, group_id),
                    )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return True, (
            f"兑换单 #{redemption_id}「{item_name}」已完成"
            if accept else
            f"兑换单 #{redemption_id}「{item_name}」已拒绝，已退款 {price} 积分"
        )

    async def shop_redemptions(
        self,
        group_id: int,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conn = await self._ensure()
        where = ["group_id = ?"]
        params: list[object] = [group_id]
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        params.append(limit)
        async with conn.execute(
            "SELECT id, user_id, item_id, item_name, price, status, user_note, note,"
            " created_at, resolved_by, resolved_at, resolve_note"
            f" FROM shop_redemptions WHERE {' AND '.join(where)}"
            " ORDER BY id DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": int(r[0]),
                "user_id": int(r[1]),
                "item_id": int(r[2]),
                "item_name": r[3],
                "price": int(r[4]),
                "status": r[5],
                "user_note": r[6] or "",
                "note": r[7] or "",
                "created_at": r[8],
                "resolved_by": r[9] or "",
                "resolved_at": r[10],
                "resolve_note": r[11] or "",
            }
            for r in rows
        ]


    async def shop_stats(self, group_id: int, days: int = 30) -> dict:
        """近 N 天商店统计：订单状态、买家数、净消耗/退款、热门商品、每日序列。"""
        days = max(1, min(365, int(days)))
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        conn = await self._ensure()
        async with conn.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(price), 0) FROM shop_redemptions"
            " WHERE group_id = ? AND created_at >= ? GROUP BY status",
            (group_id, since),
        ) as cur:
            by_status = {str(r[0]): (int(r[1]), int(r[2])) for r in await cur.fetchall()}
        async with conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM shop_redemptions"
            " WHERE group_id = ? AND created_at >= ? AND status != 'rejected'",
            (group_id, since),
        ) as cur:
            buyers = int((await cur.fetchone())[0])
        async with conn.execute(
            "SELECT item_id, item_name, COUNT(*), COALESCE(SUM(price), 0)"
            " FROM shop_redemptions WHERE group_id = ? AND created_at >= ?"
            " AND status != 'rejected' GROUP BY item_id, item_name"
            " ORDER BY COUNT(*) DESC, SUM(price) DESC LIMIT 10",
            (group_id, since),
        ) as cur:
            top = [
                {"item_id": int(r[0]), "item_name": r[1], "count": int(r[2]), "points": int(r[3])}
                for r in await cur.fetchall()
            ]
        async with conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*), COALESCE(SUM(price), 0)"
            " FROM shop_redemptions WHERE group_id = ? AND created_at >= ?"
            " AND status != 'rejected' GROUP BY day ORDER BY day",
            (group_id, since),
        ) as cur:
            daily = [
                {"day": r[0], "count": int(r[1]), "points": int(r[2])}
                for r in await cur.fetchall()
            ]
        pending = by_status.get("pending", (0, 0))[0]
        fulfilled = by_status.get("fulfilled", (0, 0))[0]
        rejected = by_status.get("rejected", (0, 0))[0]
        return {
            "days": days,
            "since": since,
            "orders": pending + fulfilled + rejected,
            "pending": pending,
            "fulfilled": fulfilled,
            "rejected": rejected,
            "buyers": buyers,
            "gross": by_status.get("pending", (0, 0))[1]
            + by_status.get("fulfilled", (0, 0))[1],
            "refunded": by_status.get("rejected", (0, 0))[1],
            "top": top,
            "daily": daily,
        }
