"""AI 长期记忆与退出记忆名单。"""

from __future__ import annotations

import json
from datetime import datetime

from src.store.base import StoreBase


class MemoryMixin(StoreBase):
    async def get_memory(self, scope: str, group_id: int, user_id: int = 0) -> dict | None:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT facts, interactions, updated_at FROM memories"
            " WHERE scope = ? AND group_id = ? AND user_id = ?",
            (scope, group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        try:
            facts = json.loads(row[0])
        except ValueError:
            facts = []
        return {"facts": [str(f) for f in facts] if isinstance(facts, list) else [],
                "interactions": int(row[1]), "updated_at": row[2]}

    async def set_memory_facts(self, scope: str, group_id: int, user_id: int, facts: list[str]) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO memories (scope, group_id, user_id, facts, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(scope, group_id, user_id) DO UPDATE SET facts = excluded.facts,"
            " updated_at = excluded.updated_at",
            (scope, group_id, user_id, json.dumps(facts, ensure_ascii=False),
             datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        await conn.commit()

    async def bump_interactions(self, group_id: int, user_id: int) -> int:
        """机器人与该成员的互动次数 +1（用于"熟悉度"），返回新值。"""
        conn = await self._ensure()
        await conn.execute(
            "INSERT INTO memories (scope, group_id, user_id, facts, interactions) VALUES ('user', ?, ?, '[]', 1)"
            " ON CONFLICT(scope, group_id, user_id) DO UPDATE SET interactions = interactions + 1",
            (group_id, user_id),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT interactions FROM memories WHERE scope = 'user' AND group_id = ? AND user_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_memories(self, group_id: int) -> list[dict]:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT scope, user_id, facts, interactions, updated_at FROM memories"
            " WHERE group_id = ? ORDER BY scope DESC, interactions DESC, user_id",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
        out = []
        for scope, uid, facts, inter, upd in rows:
            try:
                fl = json.loads(facts)
            except ValueError:
                fl = []
            out.append({"scope": scope, "user_id": uid, "facts": fl if isinstance(fl, list) else [],
                        "interactions": inter, "updated_at": upd})
        return out

    async def list_user_memories(self, user_id: int) -> list[dict]:
        """某成员在各个群里的印象（供 /记忆 在私聊中完整展示）。"""
        conn = await self._ensure()
        async with conn.execute(
            "SELECT group_id, facts, interactions FROM memories WHERE scope = 'user' AND user_id = ?"
            " ORDER BY interactions DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        out = []
        for gid, facts, inter in rows:
            try:
                fl = json.loads(facts)
            except ValueError:
                fl = []
            out.append({"group_id": gid, "facts": fl if isinstance(fl, list) else [], "interactions": inter})
        return out

    async def delete_memory(self, scope: str, group_id: int, user_id: int = 0) -> bool:
        conn = await self._ensure()
        cur = await conn.execute(
            "DELETE FROM memories WHERE scope = ? AND group_id = ? AND user_id = ?",
            (scope, group_id, user_id),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def delete_user_memories(self, user_id: int) -> int:
        """删除某成员在所有群里的印象（含互动次数）。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "DELETE FROM memories WHERE scope = 'user' AND user_id = ?", (user_id,)
        )
        await conn.commit()
        return cur.rowcount

    async def set_memory_optout(self, user_id: int, optout: bool) -> None:
        conn = await self._ensure()
        if optout:
            await conn.execute(
                "INSERT OR REPLACE INTO memory_optout (user_id, ts) VALUES (?, ?)",
                (user_id, datetime.now().astimezone().isoformat(timespec="seconds")),
            )
        else:
            await conn.execute("DELETE FROM memory_optout WHERE user_id = ?", (user_id,))
        await conn.commit()

    async def memory_optouts(self) -> set[int]:
        conn = await self._ensure()
        async with conn.execute("SELECT user_id FROM memory_optout") as cur:
            return {int(r[0]) for r in await cur.fetchall()}
