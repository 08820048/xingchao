"""官网与 Web 管理面板：挂载在 NoneBot 的 FastAPI 应用上（与反向 WS 共用 8080 端口）。

- 页面：/（重定向到官网 https://xingchao.dev）、/panel（管理面板）
- 认证：XINGCHAO_PANEL_PASSWORD，留空则启动时随机生成并打印到日志；
  登录成功后种 HttpOnly Cookie（sha256(password)）
- 功能：运行状态 / 活跃统计 / 群日志查看 / 关键词词库编辑 / 白名单管理
- 安全约定：面板仅经 compose 映射 127.0.0.1:8081（SSH 隧道访问），不暴露公网
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from nonebot import get_driver, get_loaded_plugins
from nonebot.log import logger

from src.config import get_config
from src.permission import (
    add_runtime_group,
    add_runtime_superuser,
    disabled_groups,
    merged_whitelist,
    remove_runtime_group,
    remove_runtime_superuser,
    runtime_superuser_ids,
    set_group_enabled,
    superuser_ids,
)
from src.store import get_store

_STARTED_AT = time.monotonic()
_COOKIE_NAME = "xingchao_panel"
_password_sha256: str = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _init_password() -> None:
    global _password_sha256
    cfg = get_config()
    if cfg.xingchao_panel_password:
        _password_sha256 = _sha256(cfg.xingchao_panel_password)
        logger.info("Web 面板密码来自 XINGCHAO_PANEL_PASSWORD")
    else:
        raw = secrets.token_urlsafe(12)
        _password_sha256 = _sha256(raw)
        logger.warning(
            f"未设置 XINGCHAO_PANEL_PASSWORD，已随机生成面板密码：{raw} "
            "（仅本次启动有效，建议写入 .env）"
        )


def _authorized(request: Request) -> bool:
    return request.cookies.get(_COOKIE_NAME, "") == _password_sha256


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "未登录或会话失效"}, status_code=401)


# ---------------------------------------------------------------- 视图数据


async def _status_payload() -> dict[str, Any]:
    from src.plugins import reply as reply_plugin

    cfg = get_config()
    store = get_store()
    items = reply_plugin.get_items()
    reply_enabled = await store.get_kv("reply_enabled")
    welcome_enabled = await store.get_kv("welcome_enabled")
    log_dir = cfg.xingchao_log_dir
    log_files = sorted(log_dir.glob("group-*.jsonl")) if log_dir.exists() else []
    names = sorted(
        p.name
        for p in get_loaded_plugins()
        if p.module is not None and p.module.__name__.startswith("src.plugins")
    )
    return {
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
        "plugins": names,
        "whitelist": sorted(merged_whitelist()),
        "disabled_groups": sorted(disabled_groups()),
        "replies": len(items),
        "reply_enabled": reply_enabled != "false",
        "welcome_enabled": welcome_enabled != "false",
        "log_files": [f.name for f in log_files],
        "today": datetime.now().astimezone().strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------- 路由注册


def _register_routes() -> None:
    app = get_driver().server_app

    @app.get("/panel/qq-login-qr")
    async def panel_qq_login_qr() -> Response:
        """NapCat 最新登录二维码（文件由 NapCat 挂载共享，始终为最新）。"""
        from fastapi.responses import FileResponse

        qr = Path("/napcat-cache/qrcode.png")
        if not qr.is_file():
            return Response(status_code=404)
        return FileResponse(qr, media_type="image/png")

    @app.post("/panel/api/login")
    async def panel_login(request: Request) -> JSONResponse:
        body = await request.json()
        password = str(body.get("password", ""))
        if not _password_sha256 or _sha256(password) != _password_sha256:
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

    @app.get("/panel/api/replies")
    async def panel_replies_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import reply as reply_plugin

        return JSONResponse(
            {"ok": True, "data": {"items": reply_plugin.get_items(), "enabled": reply_plugin.is_enabled()}}
        )

    @app.post("/panel/api/replies")
    async def panel_replies_save(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import reply as reply_plugin

        body = await request.json()
        items = body.get("items")
        if not isinstance(items, list):
            return JSONResponse({"ok": False, "error": "items 必须是数组"}, status_code=400)
        seen_ids: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            rid = str(raw.get("id", "")).strip()
            pattern = str(raw.get("pattern", ""))
            reply_text = str(raw.get("reply", ""))
            match = str(raw.get("match", "exact"))
            if not rid or not pattern or not reply_text:
                return JSONResponse(
                    {"ok": False, "error": f"词条 {rid or '(无 id)'} 缺少 id/pattern/reply"}, status_code=400
                )
            if rid in seen_ids:
                return JSONResponse({"ok": False, "error": f"词条 id 重复：{rid}"}, status_code=400)
            if match not in ("exact", "contains"):
                return JSONResponse(
                    {"ok": False, "error": f"词条 {rid} 的 match 只支持 exact/contains"}, status_code=400
                )
            seen_ids.add(rid)
            cooldown = float(raw.get("cooldown", get_config().xingchao_reply_cooldown))
            cleaned.append(
                {
                    "id": rid,
                    "enabled": bool(raw.get("enabled", True)),
                    "match": match,
                    "pattern": pattern,
                    "reply": reply_text,
                    "cooldown": max(0.0, cooldown),
                }
            )
        path: Path = get_config().xingchao_replies_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": 1, "items": cleaned}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.exception("面板保存词库失败")
            return JSONResponse({"ok": False, "error": "写文件失败"}, status_code=500)
        count = reply_plugin.reload_replies()
        logger.info(f"面板保存词库：{count} 条词条")
        return JSONResponse({"ok": True, "data": {"count": count}})

    @app.get("/panel/api/welcome")
    async def panel_welcome_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import groupadmin as groupadmin_plugin

        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "enabled": await groupadmin_plugin.is_welcome_enabled(),
                    "text": await groupadmin_plugin.get_welcome_text(),
                },
            }
        )

    @app.get("/panel/api/superusers")
    async def panel_superusers_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        env_ids = get_config().xingchao_superusers
        superusers = [
            {"qq": u, "source": "env" if u in env_ids else "runtime"}
            for u in sorted(superuser_ids() | runtime_superuser_ids())
        ]
        return JSONResponse({"ok": True, "data": {"superusers": superusers}})

    @app.post("/panel/api/superusers")
    async def panel_superusers_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        action = str(body.get("action", ""))
        qq = body.get("qq")
        if not isinstance(qq, int):
            return JSONResponse({"ok": False, "error": "qq 必须是整数"}, status_code=400)
        if action == "add":
            added = await add_runtime_superuser(qq)
            if not added:
                return JSONResponse({"ok": False, "error": f"QQ {qq} 已是超管"})
            return JSONResponse({"ok": True, "data": {"message": f"已添加超管 {qq}"}})
        if action == "del":
            if qq in get_config().xingchao_superusers:
                return JSONResponse(
                    {"ok": False, "error": f"QQ {qq} 来自环境变量，请修改 XINGCHAO_SUPERUSERS 后重启"}
                )
            removed = await remove_runtime_superuser(qq)
            if not removed:
                return JSONResponse({"ok": False, "error": f"QQ {qq} 不在运行时超管中"})
            return JSONResponse({"ok": True, "data": {"message": f"已移除超管 {qq}"}})
        return JSONResponse({"ok": False, "error": "action 应为 add 或 del"}, status_code=400)

    @app.get("/panel/api/ai")
    async def panel_ai_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import ai as ai_plugin

        cfg = await ai_plugin.ai_config()
        base_url, api_key = await ai_plugin._ai_creds()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "configured": await ai_plugin.is_configured(),
                    "base_url": base_url,
                    "api_key_masked": ai_plugin.mask_key(api_key),
                    "enabled": await ai_plugin.is_ai_enabled(),
                    "model": cfg["ai_model"],
                    "system_prompt": cfg["ai_system_prompt"],
                    "ctx_rounds": cfg["ai_ctx_rounds"],
                    "limit_group": cfg["ai_limit_group"],
                    "limit_user": cfg["ai_limit_user"],
                    "usage": await ai_plugin._usage_payload(),
                },
            }
        )

    @app.post("/panel/api/ai")
    async def panel_ai_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import ai as ai_plugin

        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}
        # 连接凭据（可选更新；api_key 留空 = 保持不变）
        if "base_url" in body:
            base = str(body["base_url"]).strip()
            if base and not base.startswith(("http://", "https://")):
                return JSONResponse({"ok": False, "error": "API 地址应以 http(s):// 开头"}, status_code=400)
            if base:
                updates["ai_base_url"] = base
        if "api_key" in body:
            key = str(body["api_key"]).strip()
            if key:
                if len(key) < 8 or any(c.isspace() for c in key):
                    return JSONResponse({"ok": False, "error": "API Key 格式不合法"}, status_code=400)
                updates["ai_api_key"] = key
        if "enabled" in body and not isinstance(body["enabled"], bool):
            return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
        for key in ("model", "system_prompt"):
            if key in body:
                val = str(body[key]).strip()
                if not val:
                    return JSONResponse({"ok": False, "error": f"{key} 不能为空"}, status_code=400)
                if key == "model" and (len(val) > 100 or any(c.isspace() for c in val)):
                    return JSONResponse({"ok": False, "error": "model 不合法"}, status_code=400)
                updates[f"ai_{key}"] = val
        for key in ("ctx_rounds", "limit_group", "limit_user"):
            if key in body:
                try:
                    val = int(body[key])
                except (TypeError, ValueError):
                    return JSONResponse({"ok": False, "error": f"{key} 应为整数"}, status_code=400)
                if not (1 <= val <= 10000):
                    return JSONResponse({"ok": False, "error": f"{key} 应在 1-10000 之间"}, status_code=400)
                updates[f"ai_{key}"] = str(val)
        try:
            for k, v in updates.items():
                await store.set_kv(k, v)
            if "enabled" in body:
                await store.set_kv("ai_enabled", "true" if body["enabled"] else "false")
        except Exception:
            logger.exception("面板保存 AI 配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "AI 配置已保存并生效"}})

    @app.post("/panel/api/ai/models")
    async def panel_ai_models(request: Request) -> JSONResponse:
        """拉取 OpenAI 兼容服务商支持的模型列表（GET {base}/models）。

        请求体可选 base_url / api_key：用于验证尚未保存的凭据；
        缺省时使用已保存的凭据。
        """
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import ai as ai_plugin

        body: dict = {}
        try:
            if await request.body():
                body = await request.json()
        except Exception:
            body = {}
        base, key = await ai_plugin._ai_creds()
        base = str(body.get("base_url") or base or "").strip().rstrip("/")
        key = str(body.get("api_key") or key or "").strip()
        if not (base and key):
            return JSONResponse(
                {"ok": False, "error": "请先填写 API 地址与密钥（或先保存连接）"}, status_code=400
            )
        if not base.startswith(("http://", "https://")):
            return JSONResponse({"ok": False, "error": "API 地址应以 http(s):// 开头"}, status_code=400)
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        except Exception as e:
            logger.warning(f"面板获取 AI 模型列表失败：{type(e).__name__}")
            return JSONResponse({"ok": False, "error": f"连接服务商失败：{type(e).__name__}"}, status_code=502)
        if resp.status_code != 200:
            return JSONResponse(
                {"ok": False, "error": f"服务商返回 HTTP {resp.status_code}，请检查地址与密钥"},
                status_code=502,
            )
        try:
            data = resp.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "响应不是合法 JSON"}, status_code=502)
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return JSONResponse({"ok": False, "error": "响应格式不符合 OpenAI /models 规范"}, status_code=502)
        models = sorted(
            {
                str(m.get("id") or m.get("model") or "")
                for m in items
                if isinstance(m, dict) and (m.get("id") or m.get("model"))
            }
        )
        return JSONResponse({"ok": True, "data": {"models": models}})

    @app.post("/panel/api/welcome")
    async def panel_welcome_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        enabled = body.get("enabled")
        text = body.get("text")
        if not isinstance(enabled, bool) or not isinstance(text, str):
            return JSONResponse(
                {"ok": False, "error": "enabled 应为布尔值，text 应为字符串"}, status_code=400
            )
        text = text.strip()
        if not text:
            return JSONResponse({"ok": False, "error": "欢迎语不能为空"}, status_code=400)
        if len(text) > 1000:
            return JSONResponse({"ok": False, "error": "欢迎语过长（上限 1000 字）"}, status_code=400)
        store = get_store()
        try:
            await store.set_kv("welcome_enabled", "true" if enabled else "false")
            await store.set_kv("welcome_text", text)
        except Exception:
            logger.exception("面板保存欢迎配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "进群欢迎配置已保存并生效"}})

    @app.get("/panel/api/sensitive")
    async def panel_sensitive_get(request: Request, group_id: int | None = None) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.permission import merged_whitelist
        from src.plugins import sensitive as sensitive_plugin

        raw = await sensitive_plugin.sensitive_config(group_id)
        cfg = {
            "enabled": raw["sensitive_enabled"],
            "words": raw["sensitive_words"],
            "mute_minutes": raw["sensitive_mute_minutes"],
            "notify": raw["sensitive_notify"],
        }
        overrides = await sensitive_plugin._group_overrides()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "groups": sorted(merged_whitelist()),
                    "override": overrides.get(group_id) if group_id else None,
                    **cfg,
                },
            }
        )

    @app.post("/panel/api/sensitive")
    async def panel_sensitive_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import sensitive as sensitive_plugin

        body = await request.json()
        group_id = body.get("group_id")
        if group_id is not None and not isinstance(group_id, int):
            return JSONResponse({"ok": False, "error": "group_id 应为整数"}, status_code=400)
        if body.get("clear_group") and group_id is not None:
            cleared = await sensitive_plugin.clear_group_override(group_id)
            msg = f"群 {group_id} 已恢复继承全局配置" if cleared else f"群 {group_id} 本无独立配置"
            return JSONResponse({"ok": True, "data": {"message": msg}})
        updates: dict[str, Any] = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            updates["sensitive_enabled"] = body["enabled"]
        if "words" in body:
            w = str(body["words"]).strip()
            if len(w) > 5000:
                return JSONResponse({"ok": False, "error": "词库过长（上限 5000 字）"}, status_code=400)
            updates["sensitive_words"] = w
        if "mute_minutes" in body:
            try:
                mm = int(body["mute_minutes"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "mute_minutes 应为整数"}, status_code=400)
            if not (0 <= mm <= 43200):
                return JSONResponse({"ok": False, "error": "mute_minutes 应在 0-43200"}, status_code=400)
            updates["sensitive_mute_minutes"] = mm
        if "notify" in body:
            if not isinstance(body["notify"], bool):
                return JSONResponse({"ok": False, "error": "notify 应为布尔值"}, status_code=400)
            updates["sensitive_notify"] = body["notify"]
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            if group_id is not None:
                existing = (await sensitive_plugin._group_overrides()).get(group_id, {})
                existing.update(updates)
                await sensitive_plugin.save_group_override(group_id, existing)
            else:
                store = get_store()
                for k, v in updates.items():
                    if isinstance(v, bool):
                        v = "true" if v else "false"
                    await store.set_kv(k, str(v))
        except Exception:
            logger.exception("面板保存敏感词配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        scope = f"群 {group_id}" if group_id is not None else "全局默认"
        return JSONResponse({"ok": True, "data": {"message": f"敏感词配置已保存并生效（{scope}）"}})

    @app.get("/panel/api/tasks")
    async def panel_tasks_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.permission import merged_whitelist

        tasks = await get_store().list_tasks()
        return JSONResponse({"ok": True, "data": {"tasks": tasks, "groups": sorted(merged_whitelist())}})

    @app.post("/panel/api/tasks")
    async def panel_tasks_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.permission import merged_whitelist

        body = await request.json()
        action = str(body.get("action", "create"))

        if action == "delete":
            tid = body.get("id")
            if not isinstance(tid, int):
                return JSONResponse({"ok": False, "error": "id 应为整数"}, status_code=400)
            ok = await get_store().delete_task(tid)
            return JSONResponse({"ok": True, "data": {"message": "已删除" if ok else "任务不存在"}})

        if action == "update":
            tid = body.get("id")
            if not isinstance(tid, int):
                return JSONResponse({"ok": False, "error": "id 应为整数"}, status_code=400)
            fields = body.get("fields", {})
            await get_store().update_task(tid, **fields)
            return JSONResponse({"ok": True, "data": {"message": f"任务 #{tid} 已更新"}})

        # create
        group_id = body.get("group_id")
        time_ = str(body.get("time", ""))
        message = str(body.get("message", "")).strip()
        at_all = bool(body.get("at_all", False))
        repeat = str(body.get("repeat", "daily"))
        weekday = body.get("weekday")
        date_ = body.get("date")
        if group_id not in merged_whitelist():
            return JSONResponse({"ok": False, "error": "目标群必须在白名单内"}, status_code=400)
        if not (isinstance(time_, str) and re.fullmatch(r"\d{2}:\d{2}", time_) and time_ < "24:00"):
            return JSONResponse({"ok": False, "error": "时间格式应为 HH:MM"}, status_code=400)
        if not message:
            return JSONResponse({"ok": False, "error": "消息内容不能为空"}, status_code=400)
        if len(message) > 2000:
            return JSONResponse({"ok": False, "error": "消息过长（上限 2000 字）"}, status_code=400)
        if repeat not in ("daily", "weekdays", "weekend", "weekly", "once"):
            return JSONResponse({"ok": False, "error": "repeat 不合法"}, status_code=400)
        if repeat == "weekly" and (not isinstance(weekday, int) or not 0 <= weekday <= 6):
            return JSONResponse({"ok": False, "error": "weekly 需要有效 weekday（0=周一）"}, status_code=400)
        if repeat == "once" and (not isinstance(date_, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_ or "")):
            return JSONResponse({"ok": False, "error": "once 需要日期 YYYY-MM-DD"}, status_code=400)
        tid = await get_store().add_task(group_id, time_, message, at_all, repeat,
                                          weekday if repeat == "weekly" else None,
                                          date_ if repeat == "once" else None)
        return JSONResponse({"ok": True, "data": {"message": f"定时任务 #{tid} 已创建", "id": tid}})

    @app.get("/panel/api/join")
    async def panel_join_get(request: Request, group_id: int | None = None) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.permission import merged_whitelist
        from src.plugins import request as request_plugin

        raw_cfg = await request_plugin.join_config(group_id)
        cfg = {
            "mode": raw_cfg["join_mode"],
            "question": raw_cfg["join_question"],
            "fallback": raw_cfg["join_fallback"],
            "keywords": raw_cfg["join_keywords"],
            "leave_report": raw_cfg["leave_report"],
        }
        overrides = await request_plugin._group_overrides()
        pending = [
            {"seq": seq, "group_id": req["group_id"], "user_id": req["user_id"],
             "comment": req["comment"]}
            for seq, req in sorted(request_plugin._PENDING.items())
        ]
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "groups": sorted(merged_whitelist()),
                    "override": overrides.get(group_id) if group_id else None,
                    "pending": pending,
                    **cfg,
                },
            }
        )

    @app.post("/panel/api/join")
    async def panel_join_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import request as request_plugin

        body = await request.json()
        store = get_store()
        group_id = body.get("group_id")
        if group_id is not None and not isinstance(group_id, int):
            return JSONResponse({"ok": False, "error": "group_id 应为整数"}, status_code=400)
        # 清除群独立配置，恢复继承全局
        if body.get("clear_group") and group_id is not None:
            cleared = await request_plugin.clear_group_override(group_id)
            msg = f"群 {group_id} 已恢复继承全局配置" if cleared else f"群 {group_id} 本无独立配置"
            return JSONResponse({"ok": True, "data": {"message": msg}})
        updates: dict[str, str] = {}
        if "mode" in body:
            if body["mode"] not in ("ai", "manual", "auto_approve", "auto_reject"):
                return JSONResponse({"ok": False, "error": "mode 不合法"}, status_code=400)
            updates["join_mode"] = body["mode"]
        if "fallback" in body:
            if body["fallback"] not in ("manual", "approve", "reject"):
                return JSONResponse({"ok": False, "error": "fallback 不合法"}, status_code=400)
            updates["join_fallback"] = body["fallback"]
        if "question" in body:
            q = str(body["question"]).strip()
            if not q or len(q) > 200:
                return JSONResponse({"ok": False, "error": "验证问题需 1-200 字"}, status_code=400)
            updates["join_question"] = q
        if "keywords" in body:
            kws = str(body["keywords"]).strip()
            if len(kws) > 500:
                return JSONResponse({"ok": False, "error": "关键词过长"}, status_code=400)
            updates["join_keywords"] = kws
        if "leave_report" in body:
            if not isinstance(body["leave_report"], bool):
                return JSONResponse({"ok": False, "error": "leave_report 应为布尔值"}, status_code=400)
            updates["leave_report"] = "true" if body["leave_report"] else "false"
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            if group_id is not None:
                # 按群保存：允许部分字段覆盖，未传字段保持全局
                existing = (await request_plugin._group_overrides()).get(group_id, {})
                existing.update(updates)
                await request_plugin.save_group_override(group_id, existing)
            else:
                for k, v in updates.items():
                    await store.set_kv(k, v)
        except Exception:
            logger.exception("面板保存加群审批配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        scope = f"群 {group_id}" if group_id is not None else "全局默认"
        return JSONResponse({"ok": True, "data": {"message": f"加群审批配置已保存并生效（{scope}）"}})

    @app.post("/panel/api/join/resolve")
    async def panel_join_resolve(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import request as request_plugin

        body = await request.json()
        seq, approve = body.get("seq"), body.get("approve")
        if not isinstance(seq, int) or not isinstance(approve, bool):
            return JSONResponse({"ok": False, "error": "seq 应为整数，approve 应为布尔值"}, status_code=400)
        req = request_plugin._PENDING.get(seq)
        if req is None:
            return JSONResponse({"ok": False, "error": f"没有找到申请 #{seq}"}, status_code=404)
        try:
            # resolve 在面板请求上下文中执行，需要 bot 实例
            from nonebot import get_bot
            bot = get_bot()
            await bot.call_api(
                "set_group_add_request",
                flag=req["flag"], sub_type=req["sub_type"], approve=approve,
                reason="不符合入群要求" if not approve else "",
            )
        except Exception as e:
            logger.exception("面板审批入群申请失败")
            return JSONResponse({"ok": False, "error": f"审批失败：{e}"}, status_code=500)
        request_plugin._PENDING.pop(seq, None)
        verb = "通过" if approve else "拒绝"
        return JSONResponse({"ok": True, "data": {"message": f"已{verb}申请 #{seq}（QQ {req['user_id']}）"}})

    @app.post("/panel/api/modules")
    async def panel_modules_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import reply as reply_plugin

        body = await request.json()
        key = str(body.get("key", ""))
        enabled = body.get("enabled")
        if key not in ("reply", "welcome") or not isinstance(enabled, bool):
            return JSONResponse(
                {"ok": False, "error": "key 应为 reply/welcome，enabled 应为布尔值"}, status_code=400
            )
        try:
            await get_store().set_kv(f"{key}_enabled", "true" if enabled else "false")
        except Exception:
            logger.exception("面板写入模块开关失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        if key == "reply":
            reply_plugin.set_enabled(enabled)
        return JSONResponse(
            {"ok": True, "data": {"message": f"{'关键词模块' if key == 'reply' else '进群欢迎'}已{'开启' if enabled else '关闭'}"}}
        )

    @app.get("/panel/api/groups")
    async def panel_groups_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        cfg = get_config()
        env_groups = cfg.xingchao_group_whitelist
        disabled = disabled_groups()
        groups = [
            {
                "group_id": g,
                "source": "env" if g in env_groups else "runtime",
                "disabled": g in disabled,
            }
            for g in sorted(merged_whitelist())
        ]
        return JSONResponse({"ok": True, "data": {"groups": groups}})

    @app.post("/panel/api/groups")
    async def panel_groups_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        action = str(body.get("action", ""))
        gid = body.get("group_id")
        if not isinstance(gid, int):
            return JSONResponse({"ok": False, "error": "group_id 必须是整数"}, status_code=400)
        if action == "add":
            added = await add_runtime_group(gid)
            if not added:
                return JSONResponse({"ok": False, "error": f"群 {gid} 已在白名单中"})
            return JSONResponse({"ok": True, "data": {"message": f"已添加群 {gid}"}})
        if action == "del":
            if gid in get_config().xingchao_group_whitelist:
                return JSONResponse(
                    {"ok": False, "error": f"群 {gid} 来自环境变量，请修改 XINGCHAO_GROUP_WHITELIST 后重启"}
                )
            removed = await remove_runtime_group(gid)
            if not removed:
                return JSONResponse({"ok": False, "error": f"群 {gid} 不在运行时白名单中"})
            return JSONResponse({"ok": True, "data": {"message": f"已移除群 {gid}"}})
        if action in ("on", "off"):
            changed = await set_group_enabled(gid, action == "on")
            state = "开启" if action == "on" else "关闭"
            if not changed:
                return JSONResponse({"ok": False, "error": f"群 {gid} 已处于{state}状态"})
            return JSONResponse({"ok": True, "data": {"message": f"已{state}群 {gid} 的业务"}})
        return JSONResponse({"ok": False, "error": "action 应为 add / del / on / off"}, status_code=400)

    # 官网部署在 Cloudflare Pages（独立仓库 xingchao_site），bot 只负责管理面板（base=/panel/）。
    # mount 必须放在所有 API 路由之后。
    from fastapi.staticfiles import StaticFiles

    base_dir = Path(__file__).resolve().parents[2]
    dist_dir = base_dir / "web" / "dist"

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("https://xingchao.dev", status_code=302)

    @app.get("/panel", response_model=None)
    @app.get("/panel/", response_model=None)
    async def panel_page() -> FileResponse | HTMLResponse:
        if dist_dir.is_dir():
            return FileResponse(dist_dir / "index.html")
        return HTMLResponse(_PAGE)

    if dist_dir.is_dir():
        app.mount("/panel", StaticFiles(directory=str(dist_dir), html=True), name="panel")
        logger.info(f"Web 管理面板（React 构建）已挂载：/panel（{dist_dir}）")
    else:
        logger.info("Web 管理面板（内嵌单页）已挂载：/panel（未找到 web/dist）")


def _setup() -> None:
    _init_password()
    try:
        _register_routes()
    except Exception:
        logger.exception("Web 管理面板挂载失败（当前驱动可能不是 FastAPI）")


_setup()

# ---------------------------------------------------------------- 前端页面

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>星潮 · 管理面板</title>
<style>
  :root { --bg:#0f1420; --card:#1a2130; --line:#2a3347; --fg:#e8edf7; --sub:#93a0b8;
          --acc:#6ea8fe; --ok:#5ad19c; --bad:#ff7b7b; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg); font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:20px; margin-bottom:4px; }
  .sub { color:var(--sub); font-size:12px; margin-bottom:20px; }
  .tabs { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  .tabs button { background:var(--card); color:var(--sub); border:1px solid var(--line);
                 padding:8px 16px; border-radius:8px; cursor:pointer; font-size:13px; }
  .tabs button.on { color:var(--fg); border-color:var(--acc); background:#20304d; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }
  .kv { display:flex; flex-wrap:wrap; gap:10px 24px; }
  .kv div span { display:block; color:var(--sub); font-size:12px; }
  .kv div b { font-size:18px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--sub); font-weight:500; }
  input, select, textarea { background:#111726; color:var(--fg); border:1px solid var(--line);
        border-radius:6px; padding:6px 8px; font-size:13px; }
  button.act { background:var(--acc); color:#0b1220; border:0; border-radius:6px; padding:6px 14px;
               cursor:pointer; font-size:13px; }
  button.mini { background:#20304d; color:var(--fg); border:1px solid var(--line); border-radius:6px;
                padding:4px 10px; cursor:pointer; font-size:12px; }
  .tag { font-size:11px; padding:1px 8px; border-radius:10px; }
  .tag.env { background:#233a2c; color:var(--ok); }
  .tag.rt { background:#20304d; color:var(--acc); }
  .tag.off { background:#3a2323; color:var(--bad); }
  pre.log { background:#111726; border:1px solid var(--line); border-radius:8px; padding:10px;
            max-height:420px; overflow:auto; font-size:12px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
  .muted { color:var(--sub); }
  #msg { position:fixed; top:14px; right:14px; padding:8px 16px; border-radius:8px; display:none;
         background:#20304d; border:1px solid var(--acc); }
  .item { border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:8px; }
  .item .grid { display:grid; grid-template-columns:110px 1fr 110px 90px 70px 32px; gap:6px; align-items:center; }
  @media (max-width:640px){ .item .grid { grid-template-columns:1fr 1fr; } }
</style>
</head>
<body>
<div id="msg"></div>
<div class="wrap" id="app"></div>
<script>
const $ = (h) => { const d = document.createElement('div'); d.innerHTML = h; return d.firstElementChild; };
const msg = (t, ok=true) => { const m = document.getElementById('msg');
  m.textContent = t; m.style.display='block'; m.style.borderColor = ok?'var(--acc)':'var(--bad)';
  setTimeout(()=>m.style.display='none', 2500); };
const fmtDur = (s) => { const h=Math.floor(s/3600), m=Math.floor(s%3600/60);
  return (h? h+' 小时 ':'') + m + ' 分'; };

async function api(path, opt={}) {
  const r = await fetch(path, opt);
  if (r.status === 401) { renderLogin(); throw new Error('unauthorized'); }
  return r.json();
}
const post = (p, body) => api(p, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

/* ---------- 登录 ---------- */
function renderLogin() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  const card = $(`<div class="card" style="max-width:360px;margin:80px auto;text-align:center">
    <h1>星潮 · 管理面板</h1>
    <p class="sub">请输入面板密码</p>
    <input id="pwd" type="password" style="width:100%;margin:10px 0" placeholder="面板密码">
    <button class="act" style="width:100%" onclick="doLogin()">登 录</button>
  </div>`);
  app.appendChild(card);
  card.querySelector('#pwd').addEventListener('keydown', e => { if (e.key==='Enter') doLogin(); });
}
async function doLogin() {
  const r = await fetch('/panel/api/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: document.getElementById('pwd').value})});
  if (r.ok) { renderMain(); } else { msg((await r.json()).error || '登录失败', false); }
}

/* ---------- 主界面 ---------- */
function renderMain() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  // 注意：$() 只返回 firstElementChild，必须用一个根元素包住全部内容
  app.appendChild($(`<div>
    <h1>星潮 · 管理面板</h1><p class="sub" id="st"></p>
    <div class="tabs">
      <button data-t="status" class="on">状态</button>
      <button data-t="stats">统计</button>
      <button data-t="logs">日志</button>
      <button data-t="replies">词库</button>
      <button data-t="groups">白名单</button>
    </div><div id="view"></div>
  </div>`));
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); switchTab(b.dataset.t);
  });
  switchTab('status');
}
function switchTab(t) {
  const v = document.getElementById('view'); v.innerHTML = '';
  if (t==='status') loadStatus(); if (t==='stats') loadStats();
  if (t==='logs') loadLogs(); if (t==='replies') loadReplies();
  if (t==='groups') loadGroups();
}

/* ---------- 状态 ---------- */
async function loadStatus() {
  const r = await api('/panel/api/status'); if (!r.ok) return msg(r.error,false);
  const d = r.data;
  document.getElementById('st').textContent = '数据日 ' + d.today;
  document.getElementById('view').appendChild($(`<div class="card"><div class="kv">
    <div><span>运行时长</span><b>${fmtDur(d.uptime_seconds)}</b></div>
    <div><span>白名单群</span><b>${d.whitelist.length}</b></div>
    <div><span>词条数</span><b>${d.replies}</b></div>
    <div><span>关键词模块</span><b class="tag ${d.reply_enabled?'':'off'}">${d.reply_enabled?'开启':'关闭'}</b></div>
    <div><span>进群欢迎</span><b class="tag ${d.welcome_enabled?'':'off'}">${d.welcome_enabled?'开启':'关闭'}</b></div>
    <div><span>插件</span><b style="font-size:13px">${d.plugins.join(' / ')}</b></div>
  </div></div>`));
}

/* ---------- 统计 ---------- */
async function loadStats(day) {
  day = day || new Date().toISOString().slice(0,10);
  const v = document.getElementById('view');
  v.innerHTML = '';
  const bar = $(`<div class="row"><input type="date" id="d" value="${day}">
     <button class="act" onclick="loadStats(document.getElementById('d').value)">查询</button></div>`);
  v.appendChild(bar);
  const r = await api('/panel/api/stats?day='+day); if (!r.ok) return msg(r.error,false);
  if (!r.data.groups.length) { v.appendChild($(`<div class="card muted">当日暂无消息记录</div>`)); return; }
  for (const g of r.data.groups) {
    const rows = g.top.map((t,i)=>`<tr><td>${i+1}</td><td>${t[0]}</td><td>${t[1]} 条</td></tr>`).join('');
    v.appendChild($(`<div class="card">
      <b>群 ${g.group_id}</b> <span class="muted">消息 ${g.total} · 参与 ${g.users} 人</span>
      <table style="margin-top:8px"><tr><th>#</th><th>用户</th><th>发言</th></tr>${rows}</table>
    </div>`));
  }
}

/* ---------- 日志 ---------- */
let LOGFILES = [];
async function loadLogs() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/logfiles'); if (!r.ok) return;
  LOGFILES = r.data;
  const opts = LOGFILES.map(f=>`<option>${f.name}</option>`).join('');
  v.appendChild($(`<div class="card">
    <div class="row"><select id="lf">${opts}</select>
      <input id="tail" type="number" value="200" style="width:90px" title="行数">
      <button class="act" onclick="showLog()">查看</button></div>
    <pre class="log" id="logbox">选择日志文件后查看</pre>
  </div>`));
}
async function showLog() {
  const name = document.getElementById('lf').value;
  const tail = document.getElementById('tail').value || 200;
  const r = await api('/panel/api/logs?name='+encodeURIComponent(name)+'&tail='+tail);
  if (!r.ok) return msg(r.error,false);
  const box = document.getElementById('logbox');
  box.textContent = r.data.records.map(x =>
    `[${(x.time||'').slice(11,19)}] 群${x.group_id} 用户${x.user_id}: ${x.raw_plain||''}`).join('\\n') || '(空)';
}

/* ---------- 词库 ---------- */
async function loadReplies() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/replies'); if (!r.ok) return;
  const card = $(`<div class="card">
    <div class="row"><b>关键词词库</b>
      <button class="mini" onclick="addItem()">+ 新增词条</button>
      <button class="act" onclick="saveReplies()">保存并热重载</button></div>
    <div id="items"></div></div>`);
  v.appendChild(card);
  const box = card.querySelector('#items');
  const draw = (items) => {
    box.innerHTML = '';
    items.forEach((it, i) => {
      const el = $(`<div class="item"><div class="grid">
        <input data-k="id" value="${it.id||''}" placeholder="id">
        <input data-k="pattern" value="${(it.pattern||'').replace(/"/g,'&quot;')}" placeholder="触发词">
        <select data-k="match">
          <option value="exact">精确</option><option value="contains">包含</option>
        </select>
        <input data-k="cooldown" type="number" step="0.5" value="${it.cooldown??8}">
        <label style="font-size:12px"><input data-k="enabled" type="checkbox" ${it.enabled!==false?'checked':''}> 启用</label>
        <button class="mini" onclick="this.closest('.item').remove()">✕</button>
        <textarea data-k="reply" placeholder="回复内容" style="grid-column:1/-1;min-height:52px">${it.reply||''}</textarea>
      </div></div>`);
      el.querySelector('select').value = it.match || 'exact';
      box.appendChild(el);
    });
  };
  card._draw = draw; draw(r.data.items);
  window.addItem = () => draw([...collectReplies(), {id:'', enabled:true, match:'exact', pattern:'', reply:'', cooldown:8}]);
  window.collectReplies = () => [...box.querySelectorAll('.item')].map(el => {
    const get = k => el.querySelector(`[data-k="${k}"]`);
    return { id:get('id').value.trim(), pattern:get('pattern').value,
             match:get('match').value, cooldown:parseFloat(get('cooldown').value)||8,
             enabled:get('enabled').checked, reply:get('reply').value };
  });
  window.saveReplies = async () => {
    const r2 = await post('/panel/api/replies', {items: window.collectReplies()});
    if (r2.ok) { msg('已保存并重载，共 '+r2.data.count+' 条'); } else { msg(r2.error||'保存失败', false); }
  };
}

/* ---------- 白名单 ---------- */
async function loadGroups() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/groups'); if (!r.ok) return;
  const card = $(`<div class="card"><b>群白名单</b>
    <div class="row" style="margin-top:10px">
      <input id="gid" type="number" placeholder="群号">
      <button class="act" onclick="groupAct('add')">添加</button></div>
    <table><tr><th>群号</th><th>来源</th><th></th></tr>
    ${r.data.groups.map(g=>`<tr>
      <td>${g.group_id}</td>
      <td><span class="tag ${g.source==='env'?'env':'rt'}">${g.source==='env'?'环境变量':'运行时'}</span></td>
      <td>${g.source==='env'?'<span class=muted>需改 env</span>':
        `<button class="mini" onclick="groupAct('del',${g.group_id})">移除</button>`}</td>
    </tr>`).join('')}</table></div>`);
  v.appendChild(card);
}
async function groupAct(action, gid) {
  gid = gid ?? parseInt(document.getElementById('gid').value);
  if (!gid) return msg('请输入群号', false);
  const r = await post('/panel/api/groups', {action, group_id: gid});
  if (r.ok) { msg(r.data.message); loadGroups(); } else { msg(r.error, false); }
}

/* ---------- 启动 ---------- */
api('/panel/api/status').then(()=>renderMain()).catch(()=>{});
</script>
</body>
</html>"""
