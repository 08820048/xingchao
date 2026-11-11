"""面板 API：AI、人设、长期记忆、主动插话。"""

from __future__ import annotations

import re
from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from src.permission import (
    merged_whitelist,
)
from src.store import get_store

from .common import (
    _authorized,
    _unauthorized,
)


def register(app) -> None:
    @app.get("/panel/api/ai")
    async def panel_ai_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import ai as ai_plugin

        cfg = await ai_plugin.ai_config(raw=True)
        base_url, api_key = await ai_plugin._ai_creds()
        ds = await ai_plugin.deepseek_settings()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "configured": await ai_plugin.is_configured(),
                    "provider": await get_store().get_kv("ai_provider") or "default",
                    "active_provider": await ai_plugin.active_provider(),
                    "deepseek": {
                        "api_key_masked": ai_plugin.mask_key(ds["api_key"]),
                        "model": ds["model"],
                        "base_url": ds["base_url"],
                    },
                    "base_url": base_url,
                    "api_key_masked": ai_plugin.mask_key(api_key),
                    "enabled": await ai_plugin.is_ai_enabled(),
                    "model": cfg["ai_model"],
                    "vision_model": cfg["ai_vision_model"],
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
        # 文本提供商：default（上面的 API 连接）/ deepseek
        if "provider" in body:
            if body["provider"] not in ("default", "deepseek"):
                return JSONResponse({"ok": False, "error": "provider 应为 default 或 deepseek"}, status_code=400)
            if body["provider"] == "deepseek" and not (
                (await ai_plugin.deepseek_settings())["api_key"] or str(body.get("deepseek_api_key", "")).strip()
            ):
                return JSONResponse({"ok": False, "error": "请先填写 DeepSeek API Key"}, status_code=400)
            updates["ai_provider"] = body["provider"]
        if "deepseek_api_key" in body:
            key = str(body["deepseek_api_key"]).strip()
            if key:
                if len(key) < 8 or any(c.isspace() for c in key):
                    return JSONResponse({"ok": False, "error": "DeepSeek API Key 格式不合法"}, status_code=400)
                updates["ai_deepseek_api_key"] = key
        if "deepseek_model" in body:
            val = str(body["deepseek_model"]).strip()
            if not val or len(val) > 100 or any(c.isspace() for c in val):
                return JSONResponse({"ok": False, "error": "deepseek_model 不合法"}, status_code=400)
            updates["ai_deepseek_model"] = val
        if "enabled" in body and not isinstance(body["enabled"], bool):
            return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
        for key in ("model", "system_prompt", "vision_model"):
            if key in body:
                val = str(body[key]).strip()
                if not val:
                    return JSONResponse({"ok": False, "error": f"{key} 不能为空"}, status_code=400)
                if key in ("model", "vision_model") and (len(val) > 100 or any(c.isspace() for c in val)):
                    return JSONResponse({"ok": False, "error": f"{key} 不合法"}, status_code=400)
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
        if body.get("provider") == "deepseek":
            ds = await ai_plugin.deepseek_settings()
            base, key = ds["base_url"], ds["api_key"]
            body = {"api_key": body.get("api_key")}  # base_url 固定为 DeepSeek 官方地址
        else:
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

    @app.get("/panel/api/proactive")
    async def panel_proactive_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import proactive as pa

        groups = {str(g): await pa.group_mode(g) for g in sorted(merged_whitelist())}
        return JSONResponse({"ok": True, "data": {
            "default_mode": await pa._default_mode(),
            "groups": groups,
            "overrides": {str(g): (await get_store().get_kv(f"proactive_mode_{g}")) in pa.PRESETS
                          for g in sorted(merged_whitelist())},
            "quiet_start": str(await pa._kv("proactive_quiet_start")),
            "quiet_end": str(await pa._kv("proactive_quiet_end")),
            "modes": pa.MODE_LABEL,
        }})

    @app.post("/panel/api/proactive")
    async def panel_proactive_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import proactive as pa

        body = await request.json()
        store = get_store()
        msgs: list[str] = []
        if "mode" in body:
            mode = str(body["mode"])
            if mode not in pa.PRESETS:
                return JSONResponse({"ok": False, "error": "mode 应为 quiet/normal/chatty"}, status_code=400)
            scope = body.get("group_id", "default")
            if scope == "default":
                await pa.set_mode(mode)
                msgs.append("全局默认档位已保存")
            else:
                if not isinstance(scope, int) or scope not in merged_whitelist():
                    return JSONResponse({"ok": False, "error": "群必须在白名单内"}, status_code=400)
                await pa.set_mode(mode, scope)
                msgs.append(f"群 {scope} 档位已保存")
        for key in ("quiet_start", "quiet_end"):
            if key in body:
                t = str(body[key]).strip()
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t):
                    return JSONResponse({"ok": False, "error": "时间格式应为 HH:MM"}, status_code=400)
                await store.set_kv(f"proactive_{key}", t)
                msgs.append("静默时段已保存")
        if body.get("clear_group") is not None:
            gid = body["clear_group"]
            if not isinstance(gid, int):
                return JSONResponse({"ok": False, "error": "clear_group 应为群号"}, status_code=400)
            await store.set_kv(f"proactive_mode_{gid}", "")
            msgs.append(f"群 {gid} 已恢复为全局默认档位")
        if not msgs:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        return JSONResponse({"ok": True, "data": {"message": "；".join(dict.fromkeys(msgs))}})

    @app.get("/panel/api/persona")
    async def panel_persona_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import persona

        return JSONResponse({"ok": True, "data": {
            "card": await persona.get_persona(), "labels": persona.LABELS,
            "defaults": persona.DEFAULT_PERSONA, "fields": list(persona.FIELDS),
        }})

    @app.post("/panel/api/persona")
    async def panel_persona_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import persona

        body = await request.json()
        if body.get("action") == "reset":
            await persona.reset_persona()
            return JSONResponse({"ok": True, "data": {"message": "人设已恢复默认"}})
        patch = persona.sanitize(body.get("card") or {})
        if not patch:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        await persona.save_persona(patch)
        return JSONResponse({"ok": True, "data": {"message": "人设已保存，下一条消息起生效"}})

    @app.get("/panel/api/memory")
    async def panel_memory_get(request: Request, group_id: int | None = None) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import memory

        groups = sorted(merged_whitelist())
        gid = group_id if group_id in groups else (groups[0] if groups else None)
        rows = await get_store().list_memories(gid) if gid is not None else []
        cfg = await memory.config()
        return JSONResponse({"ok": True, "data": {
            "enabled": cfg["memory_enabled"], "groups": groups, "group_id": gid,
            "group_facts": next((r["facts"] for r in rows if r["scope"] == "group"), []),
            "members": [r for r in rows if r["scope"] == "user"],
            "optouts": sorted(await get_store().memory_optouts()),
        }})

    @app.post("/panel/api/memory")
    async def panel_memory_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import memory

        body = await request.json()
        store = get_store()
        action = str(body.get("action", ""))
        if action == "set_enabled":
            if not isinstance(body.get("enabled"), bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            await store.set_kv("memory_enabled", "true" if body["enabled"] else "false")
            return JSONResponse({"ok": True, "data": {"message": "长期记忆已" + ("开启" if body["enabled"] else "关闭")}})
        gid = body.get("group_id")
        if not isinstance(gid, int) or gid not in merged_whitelist():
            return JSONResponse({"ok": False, "error": "群必须在白名单内"}, status_code=400)
        if action == "delete_user":
            uid = body.get("user_id")
            if not isinstance(uid, int):
                return JSONResponse({"ok": False, "error": "user_id 应为整数"}, status_code=400)
            ok = await store.delete_memory("user", gid, uid)
            return JSONResponse({"ok": True, "data": {"message": "已删除" if ok else "没有这条记忆"}})
        if action == "delete_group":
            ok = await store.delete_memory("group", gid)
            return JSONResponse({"ok": True, "data": {"message": "已清除群记忆" if ok else "没有群记忆"}})
        if action in ("set_user_facts", "set_group_facts"):
            facts = memory.clean_facts(body.get("facts"), memory.USER_FACTS_MAX if action == "set_user_facts" else memory.GROUP_FACTS_MAX)
            if action == "set_user_facts":
                uid = body.get("user_id")
                if not isinstance(uid, int):
                    return JSONResponse({"ok": False, "error": "user_id 应为整数"}, status_code=400)
                await store.set_memory_facts("user", gid, uid, facts)
            else:
                await store.set_memory_facts("group", gid, 0, facts)
            return JSONResponse({"ok": True, "data": {"message": f"已保存 {len(facts)} 条（敏感或过长的内容会被自动过滤）"}})
        return JSONResponse({"ok": False, "error": "未知 action"}, status_code=400)
