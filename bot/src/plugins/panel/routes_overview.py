"""面板 API：概览、登录、统计与日志。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import FileResponse, JSONResponse
from src.config import get_config
from src.store import get_store

from . import common
from .common import (
    _COOKIE_NAME,
    _authorized,
    _scan_hourly,
    _sha256,
    _status_payload,
    _unauthorized,
    _valid_day,
)


def register(app) -> None:
    @app.get("/panel/qq-login-qr")
    async def panel_qq_login_qr() -> Response:
        """NapCat 最新登录二维码（文件由 NapCat 挂载共享，始终为最新）。"""

        qr = Path("/napcat-cache/qrcode.png")
        if not qr.is_file():
            return Response(status_code=404)
        return FileResponse(qr, media_type="image/png")

    @app.post("/panel/api/login")
    async def panel_login(request: Request) -> JSONResponse:
        body = await request.json()
        password = str(body.get("password", ""))
        if not common._password_sha256 or _sha256(password) != common._password_sha256:
            return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
        # 注意：Cookie 必须设在返回的 Response 上，注入的 Response 会被返回值替换
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            _COOKIE_NAME, _sha256(password), httponly=True, samesite="lax", max_age=7 * 86400
        )
        return resp

    @app.post("/panel/api/logout")
    async def panel_logout(response: Response) -> JSONResponse:
        response.delete_cookie(_COOKIE_NAME)
        return JSONResponse({"ok": True})

    @app.get("/panel/api/status")
    async def panel_status(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        return JSONResponse({"ok": True, "data": await _status_payload()})

    @app.get("/panel/api/stats")
    async def panel_stats(request: Request, day: str = "") -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        day = day or datetime.now().astimezone().strftime("%Y-%m-%d")
        if not all(c.isdigit() or c == "-" for c in day) or len(day) != 10:
            return JSONResponse({"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400)
        store = get_store()
        overview = await store.get_day_overview(day)
        result = []
        for gid, total in overview:
            users = await store.get_group_day_stat(gid, day)
            top = await store.get_top_users(gid, day, 5)
            result.append(
                {"group_id": gid, "total": total, "users": users[1], "top": top}
            )
        return JSONResponse({"ok": True, "data": {"day": day, "groups": result}})

    @app.get("/panel/api/stats/trend")
    async def panel_stats_trend(
        request: Request, end: str = "", days: int = 14, group_id: int | None = None
    ) -> JSONResponse:
        """近 N 天消息量与参与人数序列（缺失日期补 0）。"""
        if not _authorized(request):
            return _unauthorized()
        end = end or datetime.now().astimezone().strftime("%Y-%m-%d")
        if not _valid_day(end):
            return JSONResponse(
                {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400
            )
        days = max(3, min(days, 90))
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        start = (end_d - timedelta(days=days - 1)).isoformat()
        series = {
            d: (t, u)
            for d, t, u in await get_store().get_day_series(start, end, group_id)
        }
        points = []
        for i in range(days):
            d = (end_d - timedelta(days=days - 1 - i)).isoformat()
            total, users = series.get(d, (0, 0))
            points.append({"day": d, "total": total, "users": users})
        return JSONResponse(
            {
                "ok": True,
                "data": {"end": end, "days": days, "group_id": group_id, "series": points},
            }
        )

    @app.get("/panel/api/stats/hourly")
    async def panel_stats_hourly(
        request: Request, day: str = "", group_id: int | None = None
    ) -> JSONResponse:
        """当日 24 小时消息分布（解析 jsonl 日志）。"""
        if not _authorized(request):
            return _unauthorized()
        day = day or datetime.now().astimezone().strftime("%Y-%m-%d")
        if not _valid_day(day):
            return JSONResponse(
                {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400
            )
        log_dir = get_config().xingchao_log_dir
        hours = await asyncio.to_thread(_scan_hourly, log_dir, day, group_id)
        return JSONResponse(
            {"ok": True, "data": {"day": day, "group_id": group_id, "hours": hours}}
        )

    @app.get("/panel/api/stats/top")
    async def panel_stats_top(
        request: Request, day: str = "", group_id: int | None = None, limit: int = 10
    ) -> JSONResponse:
        """当日发言 Top；group_id 为空时跨群汇总。"""
        if not _authorized(request):
            return _unauthorized()
        day = day or datetime.now().astimezone().strftime("%Y-%m-%d")
        if not _valid_day(day):
            return JSONResponse(
                {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400
            )
        limit = max(1, min(limit, 50))
        store = get_store()
        if group_id is not None:
            top = await store.get_top_users(group_id, day, limit)
        else:
            top = await store.get_top_users_all(day, limit)
        return JSONResponse(
            {"ok": True, "data": {"day": day, "group_id": group_id, "top": top}}
        )

    @app.get("/panel/api/logfiles")
    async def panel_logfiles(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        log_dir = get_config().xingchao_log_dir
        files = sorted(
            (f.name, f.stat().st_size) for f in log_dir.glob("group-*.jsonl")
        ) if log_dir.exists() else []
        return JSONResponse({"ok": True, "data": [{"name": n, "size": s} for n, s in files]})

    @app.get("/panel/api/logs")
    async def panel_logs(request: Request, name: str = "", tail: int = 200) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        log_dir = get_config().xingchao_log_dir
        # name 仅允许纯文件名，防目录穿越
        if not name or "/" in name or "\\" in name or ".." in name or not name.startswith("group-"):
            return JSONResponse({"ok": False, "error": "非法文件名"}, status_code=400)
        path = log_dir / name
        if not path.is_file():
            return JSONResponse({"ok": True, "data": {"records": []}})
        tail = max(1, min(tail, 1000))
        lines = path.read_text(encoding="utf-8").splitlines()[-tail:]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return JSONResponse({"ok": True, "data": {"records": records}})
