"""面板 API：敏感词、处罚、图片风控。"""

from __future__ import annotations

from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from src.permission import (
    merged_whitelist,
)
from src.store import get_store

from .common import (
    _authorized,
    _panel_bot,
    _unauthorized,
)


def register(app) -> None:
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
            "review": raw["sensitive_review"],
            "fallback": raw["sensitive_review_fallback"],
            "variant": raw["sensitive_variant_level"],
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

    @app.get("/panel/api/sensitive/stats")
    async def panel_sensitive_stats(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins.sensitive import suggest_for_word

        rows = await get_store().word_stats()
        for r in rows:
            r["suggestion"] = suggest_for_word(r)
        return JSONResponse({"ok": True, "data": {"words": rows}})

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
        if "review" in body:
            if not isinstance(body["review"], bool):
                return JSONResponse({"ok": False, "error": "review 应为布尔值"}, status_code=400)
            updates["sensitive_review"] = body["review"]
        if "fallback" in body:
            if body["fallback"] not in ("notify", "recall"):
                return JSONResponse(
                    {"ok": False, "error": "fallback 应为 notify / recall"}, status_code=400
                )
            updates["sensitive_review_fallback"] = body["fallback"]
        if "variant" in body:
            if body["variant"] not in ("basic", "normal", "pinyin"):
                return JSONResponse(
                    {"ok": False, "error": "variant 应为 basic / normal / pinyin"},
                    status_code=400,
                )
            updates["sensitive_variant_level"] = body["variant"]
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

    @app.get("/panel/api/punishments")
    async def panel_punishments_get(
        request: Request, group_id: int | None = None, limit: int = 100
    ) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        store = get_store()
        records = await store.list_punishments(limit=max(1, min(limit, 300)), group_id=group_id)
        offenders = await store.repeat_offenders(days=30, limit=10)
        appeals = await store.pending_appeals()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "records": records,
                    "offenders": offenders,
                    "appeals": appeals,
                    "groups": sorted(merged_whitelist()),
                },
            }
        )

    @app.post("/panel/api/punishments")
    async def panel_punishments_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        if body.get("action") != "resolve":
            return JSONResponse({"ok": False, "error": "action 仅支持 resolve"}, status_code=400)
        pid, approve = body.get("id"), body.get("approve")
        if not isinstance(pid, int) or not isinstance(approve, bool):
            return JSONResponse(
                {"ok": False, "error": "id 应为整数，approve 应为布尔值"}, status_code=400
            )
        from src.plugins.appeals import resolve_appeal

        note = str(body.get("note", "")).strip()[:200]
        ok, msg = await resolve_appeal(_panel_bot(), pid, approve, note, operator="面板")
        if not ok:
            return JSONResponse({"ok": False, "error": msg}, status_code=404)
        return JSONResponse({"ok": True, "data": {"message": msg}})

    @app.get("/panel/api/visionguard")
    async def panel_visionguard_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import visionguard as vg

        return JSONResponse({"ok": True, "data": await vg.vision_config()})

    @app.post("/panel/api/visionguard")
    async def panel_visionguard_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import visionguard as vg

        body = await request.json()
        updates: dict[str, str] = {}
        for key, kv_key in (
            ("enabled", "vision_guard_enabled"),
            ("notify", "vision_notify"),
            ("qrcode_enabled", "qrcode_guard_enabled"),
            ("qrcode_notify", "qrcode_notify"),
        ):
            if key in body:
                if not isinstance(body[key], bool):
                    return JSONResponse(
                        {"ok": False, "error": f"{key} 应为布尔值"}, status_code=400
                    )
                updates[kv_key] = "true" if body[key] else "false"
        for key, kv_key in (
            ("mute_minutes", "vision_mute_minutes"),
            ("cooldown", "vision_cooldown"),
            ("daily_limit", "vision_daily_limit"),
            ("qrcode_mute_minutes", "qrcode_mute_minutes"),
        ):
            if key in body:
                try:
                    num = int(body[key])
                except (TypeError, ValueError):
                    return JSONResponse({"ok": False, "error": f"{key} 应为整数"}, status_code=400)
                if not 0 <= num <= 43200:
                    return JSONResponse(
                        {"ok": False, "error": f"{key} 应在 0-43200"}, status_code=400
                    )
                updates[kv_key] = str(num)
        if "qrcode_types" in body:
            raw = body["qrcode_types"]
            types = [str(t).strip() for t in (raw if isinstance(raw, list) else str(raw).split(","))]
            types = [t for t in types if t]
            if not set(types) <= set(vg.QR_TYPE_LABEL):
                return JSONResponse(
                    {"ok": False, "error": "包含未知的二维码类型"}, status_code=400
                )
            updates["qrcode_types"] = ",".join(types)
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        store = get_store()
        for k, v in updates.items():
            await store.set_kv(k, v)
        return JSONResponse({"ok": True, "data": {"message": "图片/二维码审核配置已保存并生效"}})
