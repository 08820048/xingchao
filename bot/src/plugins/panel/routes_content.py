"""面板 API：词库、欢迎语、周报、定时任务、推送、博客。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from src.config import get_config
from src.permission import (
    merged_whitelist,
)
from src.store import get_store

from .common import (
    _authorized,
    _unauthorized,
)


def register(app) -> None:
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

    @app.get("/panel/api/report")
    async def panel_report_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import weeklyreport as report_plugin

        cfg = await report_plugin.panel_report_config()
        return JSONResponse({"ok": True, "data": {**cfg, "groups": sorted(merged_whitelist())}})

    @app.post("/panel/api/report")
    async def panel_report_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        updates: dict[str, str] = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            updates["report_enabled"] = "true" if body["enabled"] else "false"
        if "weekday" in body:
            try:
                wd = int(body["weekday"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "weekday 应为 0-6"}, status_code=400)
            if not 0 <= wd <= 6:
                return JSONResponse({"ok": False, "error": "weekday 应为 0-6"}, status_code=400)
            updates["report_weekday"] = str(wd)
        if "time" in body:
            t = str(body["time"]).strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t):
                return JSONResponse({"ok": False, "error": "时间格式应为 HH:MM"}, status_code=400)
            updates["report_time"] = t
        if "scope" in body:
            s = str(body["scope"]).strip()
            if s != "all" and not (s.isdigit() and int(s) in merged_whitelist()):
                return JSONResponse(
                    {"ok": False, "error": "scope 应为 all 或白名单群号"}, status_code=400
                )
            updates["report_scope"] = s
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        store = get_store()
        for k, v in updates.items():
            await store.set_kv(k, v)
        return JSONResponse({"ok": True, "data": {"message": "周报配置已保存并生效"}})

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

    @app.get("/panel/api/feeds")
    async def panel_feeds_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.permission import merged_whitelist
        from src.plugins import feeds as feeds_plugin

        return JSONResponse({"ok": True, "data": {
            "subs": await get_store().list_feed_subs(),
            "groups": sorted(merged_whitelist()),
            "sources": {k: v[0] for k, v in feeds_plugin.SOURCES.items()},
        }})

    @app.post("/panel/api/feeds")
    async def panel_feeds_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        import json as _json

        from nonebot import get_bot
        from src.permission import merged_whitelist
        from src.plugins import feeds as feeds_plugin

        body = await request.json()
        action = str(body.get("action", "create"))
        store = get_store()

        def bad(msg: str) -> JSONResponse:
            return JSONResponse({"ok": False, "error": msg}, status_code=400)

        if action in ("delete", "update", "push_now"):
            sid = body.get("id")
            if not isinstance(sid, int):
                return bad("id 应为整数")
            if action == "delete":
                ok = await store.delete_feed_sub(sid)
                return JSONResponse({"ok": True, "data": {"message": "已删除" if ok else "订阅不存在"}})
            if action == "update":
                fields = {k: v for k, v in (body.get("fields") or {}).items()
                          if k in ("enabled", "time", "ai_summary")}
                if "time" in fields and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(fields["time"])):
                    return bad("时间格式应为 HH:MM")
                await store.update_feed_sub(sid, **fields)
                return JSONResponse({"ok": True, "data": {"message": f"订阅 #{sid} 已更新"}})
            sub = next((s for s in await store.list_feed_subs() if s["id"] == sid), None)
            if sub is None or sub["source"] not in feeds_plugin.SOURCES:
                return bad("订阅不存在")
            try:
                bot = get_bot()
            except Exception:
                return bad("机器人当前未连接")
            sent, msg = await feeds_plugin.run_sub(bot, sub)
            return JSONResponse({"ok": sent, "data": {"message": f"#{sid}：{msg}"}, "error": msg})

        group_id = body.get("group_id")
        source = str(body.get("source", ""))
        time_ = str(body.get("time", ""))
        repeat = str(body.get("repeat", "daily"))
        weekday = body.get("weekday")
        params = body.get("params") or {}
        if group_id not in merged_whitelist():
            return bad("目标群必须在白名单内")
        if source not in feeds_plugin.SOURCES:
            return bad("内容源不合法")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_):
            return bad("时间格式应为 HH:MM")
        if repeat not in feeds_plugin.REPEATS:
            return bad("repeat 不合法")
        if repeat == "weekly" and (not isinstance(weekday, int) or not 0 <= weekday <= 6):
            return bad("weekly 需要有效 weekday（0=周一）")
        if source == "github":
            since = params.get("since", "daily")
            language = str(params.get("language", "")).strip()
            if since not in ("daily", "weekly", "monthly"):
                return bad("since 应为 daily/weekly/monthly")
            if not re.fullmatch(r"[A-Za-z0-9+#._-]{0,30}", language):
                return bad("语言参数不合法")
            params = {"since": since, "language": language}
        else:
            params = {}
        sid = await store.add_feed_sub(group_id, source, time_, repeat,
                                       weekday if repeat == "weekly" else None,
                                       _json.dumps(params, ensure_ascii=False),
                                       bool(body.get("ai_summary", False)))
        return JSONResponse({"ok": True, "data": {"message": f"内容推送 #{sid} 已创建", "id": sid}})

    @app.get("/panel/api/blog")
    async def panel_blog_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import blog as blog_plugin

        try:
            return JSONResponse({"ok": True, "data": await blog_plugin.status()})
        except Exception:
            logger.exception("读取博客知识库状态失败")
            return JSONResponse({"ok": False, "error": "读取失败"}, status_code=500)

    @app.post("/panel/api/blog")
    async def panel_blog_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import blog as blog_plugin

        body = await request.json()
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            await get_store().set_kv("blog_enabled", "true" if body["enabled"] else "false")
            return JSONResponse(
                {"ok": True,
                 "data": {"message": f"博客知识关联推荐已{'开启' if body['enabled'] else '关闭'}"}}
            )
        if body.get("refresh"):
            try:
                result = await blog_plugin.refresh(force=True)
            except Exception:
                logger.exception("面板刷新博客知识库失败")
                return JSONResponse({"ok": False, "error": "刷新失败"}, status_code=500)
            if not result.get("ok"):
                return JSONResponse(
                    {"ok": False, "error": f"刷新失败：{result.get('error', '未知错误')}（沿用旧数据）"},
                    status_code=502,
                )
            return JSONResponse(
                {"ok": True, "data": {"message": f"已刷新：共 {result['count']} 篇文章（{result.get('time', '')}）"}}
            )
        return JSONResponse({"ok": False, "error": "需要 enabled 或 refresh 字段"}, status_code=400)
