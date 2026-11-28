"""面板 API：本地按群积分（概览、账户、流水、配置、手动调分）。"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from src.permission import merged_whitelist
from src.store import get_store

from .common import _authorized, _panel_bot, _unauthorized


def _safe_cell(value):
    """防止 CSV 公式注入：以 = + - @ 开头的字符串前加单引号。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    buffer.write("\ufeff")  # Excel 识别 UTF-8
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([[_safe_cell(cell) for cell in row] for row in rows])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register(app) -> None:
    @app.get("/panel/api/points")
    async def panel_points_get(request: Request, group_id: int | None = None) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        groups = sorted(merged_whitelist())
        gid = group_id if isinstance(group_id, int) and group_id > 0 else (
            groups[0] if groups else 0
        )
        from src.plugins import points as points_plugin

        settings = await points_plugin.load_settings()
        if not gid:
            return JSONResponse({
                "ok": True,
                "data": {
                    "group_id": 0,
                    "groups": groups,
                    "overview": {"accounts": 0, "total": 0, "sign_today": 0, "ledger": 0},
                    "accounts": [],
                    "ledger": [],
                    "settings": settings,
                    "limits": points_plugin.LIMITS,
                    "display": points_plugin.DISPLAY,
                    "shop_items": [],
                    "pending_redemptions": [],
                    "shop_stats": {
                        "days": 30, "since": "", "orders": 0, "pending": 0,
                        "fulfilled": 0, "rejected": 0, "buyers": 0, "gross": 0,
                        "refunded": 0, "top": [], "daily": [],
                    },
                },
            })
        store = get_store()
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        return JSONResponse({
            "ok": True,
            "data": {
                "group_id": gid,
                "groups": groups,
                "overview": await store.points_overview(gid, day),
                "accounts": await store.points_accounts(gid, 200),
                "ledger": await store.recent_points_ledger(gid, 50),
                "settings": settings,
                "limits": points_plugin.LIMITS,
                "display": points_plugin.DISPLAY,
                "shop_items": await store.shop_items(gid, include_disabled=True),
                "pending_redemptions": await store.shop_redemptions(
                    gid, status="pending", limit=50
                ),
                "shop_stats": await store.shop_stats(gid, 30),
            },
        })

    @app.get("/panel/api/points/export")
    async def panel_points_export(
        request: Request, group_id: int = 0, kind: str = "accounts"
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if not group_id:
            return JSONResponse({"ok": False, "error": "缺少群号"}, status_code=400)
        store = get_store()
        if kind == "accounts":
            rows = [
                [r["user_id"], r["balance"], r["updated_at"]]
                for r in await store.points_accounts(group_id, 100_000)
            ]
            return _csv_response(
                f"xingchao_points_accounts_{group_id}.csv",
                ["user_id", "balance", "updated_at"], rows,
            )
        if kind == "ledger":
            rows = [
                [r["user_id"], r["delta"], r["balance_after"], r["reason"], r["ref"],
                 r["note"], r["created_at"]]
                for r in await store.recent_points_ledger(group_id, 100_000)
            ]
            return _csv_response(
                f"xingchao_points_ledger_{group_id}.csv",
                ["user_id", "delta", "balance_after", "reason", "ref", "note", "created_at"],
                rows,
            )
        if kind == "redemptions":
            rows = [
                [r["id"], r["user_id"], r["item_id"], r["item_name"], r["price"],
                 r["status"], r["user_note"], r["note"], r["created_at"],
                 r["resolved_by"], r["resolved_at"], r["resolve_note"]]
                for r in await store.shop_redemptions(group_id, limit=100_000)
            ]
            return _csv_response(
                f"xingchao_shop_redemptions_{group_id}.csv",
                ["id", "user_id", "item_id", "item_name", "price", "status",
                 "user_note", "note", "created_at", "resolved_by", "resolved_at",
                 "resolve_note"],
                rows,
            )
        if kind == "items":
            rows = [
                [r["id"], r["name"], r["description"], r["price"], r["stock"],
                 r["per_user_limit"], int(r["enabled"]), r["delivery"],
                 r["delivery_data"], r["created_at"], r["updated_at"]]
                for r in await store.shop_items(group_id, include_disabled=True)
            ]
            return _csv_response(
                f"xingchao_shop_items_{group_id}.csv",
                ["id", "name", "description", "price", "stock", "per_user_limit",
                 "enabled", "delivery", "delivery_data", "created_at", "updated_at"],
                rows,
            )
        return JSONResponse(
            {"ok": False, "error": "kind 应为 accounts / ledger / redemptions / items"},
            status_code=400,
        )

    @app.post("/panel/api/points")
    async def panel_points_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        action = str(body.get("action", ""))
        from src.plugins import points as points_plugin

        if action == "config":
            updates = body.get("updates")
            if not isinstance(updates, dict) or not updates:
                return JSONResponse(
                    {"ok": False, "error": "updates 不能为空"}, status_code=400
                )
            messages = []
            for key, value in updates.items():
                ok, text = await points_plugin.update_setting(str(key), str(value))
                if not ok:
                    return JSONResponse({"ok": False, "error": text}, status_code=400)
                messages.append(text)
            return JSONResponse({"ok": True, "data": {"message": "；".join(messages)}})

        if action == "shop_add":
            try:
                group_id = int(body.get("group_id") or 0)
                price = int(body.get("price") or 0)
                stock = int(body.get("stock") if body.get("stock") is not None else -1)
                limit = int(body.get("per_user_limit") or 0)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "价格/库存/限购必须是整数"}, status_code=400
                )
            if not group_id:
                return JSONResponse({"ok": False, "error": "缺少群号"}, status_code=400)
            ok, _item_id, text = await get_store().add_shop_item(
                group_id,
                str(body.get("name") or ""),
                price,
                description=str(body.get("description") or ""),
                stock=stock,
                per_user_limit=limit,
                delivery=str(body.get("delivery") or "manual"),
                delivery_data=str(body.get("delivery_data") or ""),
            )
            if not ok:
                return JSONResponse({"ok": False, "error": text}, status_code=400)
            return JSONResponse({"ok": True, "data": {"message": text}})

        if action == "shop_update":
            try:
                group_id = int(body.get("group_id") or 0)
                item_id = int(body.get("item_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "群号/商品编号必须是整数"}, status_code=400)
            fields = body.get("fields")
            if not group_id or not item_id or not isinstance(fields, dict) or not fields:
                return JSONResponse({"ok": False, "error": "参数不完整"}, status_code=400)
            ok, text = await get_store().update_shop_item(group_id, item_id, fields)
            if not ok:
                return JSONResponse({"ok": False, "error": text}, status_code=400)
            return JSONResponse({"ok": True, "data": {"message": text}})

        if action == "shop_resolve":
            try:
                group_id = int(body.get("group_id") or 0)
                redemption_id = int(body.get("redemption_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "群号/兑换单编号必须是整数"}, status_code=400
                )
            accept = body.get("accept")
            if not group_id or not redemption_id or not isinstance(accept, bool):
                return JSONResponse({"ok": False, "error": "参数不完整"}, status_code=400)
            from src.plugins import shop as shop_plugin

            ok, message = await shop_plugin.resolve(
                _panel_bot(), group_id, "面板", redemption_id, accept,
                str(body.get("note") or ""),
            )
            if not ok:
                return JSONResponse({"ok": False, "error": message.extract_plain_text()}, status_code=400)
            return JSONResponse({"ok": True, "data": {"message": message.extract_plain_text()}})

        if action == "adjust":
            try:
                group_id = int(body.get("group_id") or 0)
                user_id = int(body.get("user_id") or 0)
                delta = int(body.get("delta") or 0)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "group_id / user_id / delta 必须是整数"},
                    status_code=400,
                )
            reason = str(body.get("reason") or "").strip()
            if not group_id or not user_id:
                return JSONResponse(
                    {"ok": False, "error": "缺少群号或 QQ 号"}, status_code=400
                )
            if delta == 0:
                return JSONResponse(
                    {"ok": False, "error": "调整值不能为 0"}, status_code=400
                )
            if not reason:
                return JSONResponse(
                    {"ok": False, "error": "必须填写调整原因（会写入流水）"},
                    status_code=400,
                )
            ok, text = await points_plugin.adjust_points(
                group_id, "面板", user_id, delta, reason
            )
            if not ok:
                return JSONResponse({"ok": False, "error": text}, status_code=400)
            return JSONResponse({"ok": True, "data": {"message": text}})

        return JSONResponse(
            {"ok": False, "error": "action 应为 config / adjust / shop_add / shop_update / shop_resolve"}, status_code=400
        )
