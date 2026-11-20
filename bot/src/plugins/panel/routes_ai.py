"""面板 API：AI、人设、长期记忆、主动插话。"""

from __future__ import annotations

import json
import re
from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from src import llm
from src.httpclient import get_http
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

        cfg = await llm.ai_config(raw=True)
        base_url, api_key = await llm._ai_creds()
        ds = await llm.deepseek_settings()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "configured": await llm.is_configured(),
                    "provider": await get_store().get_kv("ai_provider") or "default",
                    "active_provider": await llm.active_provider(),
                    "deepseek": {
                        "api_key_masked": llm.mask_key(ds["api_key"]),
                        "model": ds["model"],
                        "base_url": ds["base_url"],
                    },
                    "base_url": base_url,
                    "api_key_masked": llm.mask_key(api_key),
                    "enabled": await llm.is_ai_enabled(),
                    "model": cfg["ai_model"],
                    "vision_model": cfg["ai_vision_model"],
                    "system_prompt": cfg["ai_system_prompt"],
                    "default_system_prompt": llm.DEFAULTS["ai_system_prompt"],
                    "is_default_prompt": cfg["ai_system_prompt"] == llm.DEFAULTS["ai_system_prompt"],
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

        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}
        if body.get("action") == "reset_prompt":
            # 一键恢复默认人格提示词（改过旧版“群管机器人”文案的部署用它重置）
            updates["ai_system_prompt"] = llm.DEFAULTS["ai_system_prompt"]
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
                (await llm.deepseek_settings())["api_key"] or str(body.get("deepseek_api_key", "")).strip()
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

    @app.get("/panel/api/topics")
    async def panel_topics_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import topics

        return JSONResponse({"ok": True, "data": await topics.panel_payload()})

    @app.post("/panel/api/topics")
    async def panel_topics_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import topics

        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            updates["topics_enabled"] = "true" if body["enabled"] else "false"
        for item in body.get("categories") or []:
            key = str(item.get("key") or "")
            if key not in topics.CATEGORIES:
                return JSONResponse({"ok": False, "error": f"未知分类 {key}"}, status_code=400)
            if "enabled" in item:
                if not isinstance(item["enabled"], bool):
                    return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
                updates[f"topics_{key}_enabled"] = "true" if item["enabled"] else "false"
            if "words" in item:
                words = str(item["words"] or "").strip()
                if len(words) > topics.MAX_WORDS:
                    return JSONResponse(
                        {"ok": False, "error": f"词表过长（上限 {topics.MAX_WORDS} 字）"},
                        status_code=400,
                    )
                updates[f"topics_{key}_words"] = words
            if "reply" in item:
                reply = str(item["reply"] or "").strip()
                if not reply:
                    return JSONResponse({"ok": False, "error": "话术不能为空"}, status_code=400)
                if len(reply) > topics.MAX_REPLY_CHARS:
                    return JSONResponse(
                        {"ok": False, "error": f"话术过长（上限 {topics.MAX_REPLY_CHARS} 字）"},
                        status_code=400,
                    )
                updates[f"topics_{key}_reply"] = reply
        if body.get("test"):
            hit = await topics.classify_async(str(body["test"]))
            return JSONResponse({"ok": True, "data": {
                "message": f"命中「{topics.CATEGORIES[hit[0]]}」（{hit[1]}）" if hit else "没有命中",
                "hit": {"category": hit[0], "word": hit[1]} if hit else None,
                "reply": await topics.reply_for(hit[0]) if hit else "",
            }})
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            for key, value in updates.items():
                await store.set_kv(key, value)
        except Exception:
            logger.exception("面板保存话题规约失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "话题规约已保存并生效"}})

    @app.get("/panel/api/worklog")
    async def panel_worklog_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import worklog

        data = await worklog.panel_payload()
        data["preview"] = await worklog.build_worklog(None, days=7)
        return JSONResponse({"ok": True, "data": data})

    @app.post("/panel/api/worklog")
    async def panel_worklog_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}
        for flag, field in (("enabled", "worklog_enabled"), ("weekly", "worklog_weekly")):
            if flag in body:
                if not isinstance(body[flag], bool):
                    return JSONResponse({"ok": False, "error": f"{flag} 应为布尔值"}, status_code=400)
                updates[field] = "true" if body[flag] else "false"
        if "weekday" in body:
            try:
                wd = int(body["weekday"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "weekday 应为 0-6"}, status_code=400)
            if not 0 <= wd <= 6:
                return JSONResponse({"ok": False, "error": "weekday 应为 0-6"}, status_code=400)
            updates["worklog_weekday"] = str(wd)
        if "time" in body:
            import re as _re

            t = str(body["time"]).strip()
            if not _re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t):
                return JSONResponse({"ok": False, "error": "时间格式应为 HH:MM"}, status_code=400)
            updates["worklog_time"] = t
        if "scope" in body:
            scope = str(body["scope"]).strip()
            if scope != "all" and not (scope.isdigit() and int(scope) in merged_whitelist()):
                return JSONResponse({"ok": False, "error": "scope 应为 all 或白名单群号"}, status_code=400)
            updates["worklog_scope"] = scope
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            for key, value in updates.items():
                await store.set_kv(key, value)
        except Exception:
            logger.exception("面板保存工作台账配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "工作台账配置已保存并生效"}})

    @app.get("/panel/api/kb")
    async def panel_kb_get(request: Request, group_id: int | None = None) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import kb

        data = await kb.panel_payload(group_id)
        data["suggestions"] = await kb.suggest(group_id) if group_id else []
        return JSONResponse({"ok": True, "data": data})

    @app.post("/panel/api/kb")
    async def panel_kb_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import kb

        body = await request.json()
        store = get_store()
        action = str(body.get("action") or "").strip()

        if action in ("add", "update", "delete"):
            group_id = body.get("group_id")
            if action != "delete":
                if not isinstance(group_id, int) or not group_id:
                    return JSONResponse({"ok": False, "error": "group_id 不合法"}, status_code=400)
            if action == "add":
                question = str(body.get("question") or "").strip()
                answer = str(body.get("answer") or "").strip()
                if not (question and answer):
                    return JSONResponse({"ok": False, "error": "问题与答案都不能为空"}, status_code=400)
                entry_id = await store.add_kb_entry(
                    group_id, question[:200], answer[:2000],
                    str(body.get("keywords") or "")[:300], source="panel",
                )
                return JSONResponse({"ok": True, "data": {"message": f"已添加知识 #{entry_id}", "id": entry_id}})
            entry_id = body.get("id")
            if not isinstance(entry_id, int):
                return JSONResponse({"ok": False, "error": "id 不合法"}, status_code=400)
            if action == "delete":
                ok = await store.delete_kb_entry(entry_id)
                return JSONResponse({"ok": ok, "data": {"message": "已删除"}} if ok
                                    else {"ok": False, "error": "没有这条知识"})
            fields = {k: body[k] for k in ("question", "answer", "keywords") if k in body}
            ok = await store.update_kb_entry(entry_id, **fields)
            return JSONResponse({"ok": ok, "data": {"message": "已保存"}} if ok
                                else {"ok": False, "error": "没有可更新的字段"})

        if action == "test":
            query = str(body.get("query") or "").strip()
            group_id = body.get("group_id")
            if not (query and isinstance(group_id, int) and group_id):
                return JSONResponse({"ok": False, "error": "需要 query 与 group_id"}, status_code=400)
            entry = await kb.match(group_id, query, min_score=0.0)
            return JSONResponse({"ok": True, "data": {
                "message": "命中" if entry else "没有命中",
                "entry": entry,
            }})

        updates: dict[str, str] = {}
        for flag, field in (("enabled", "kb_enabled"), ("auto_answer", "kb_auto_answer"),
                            ("require_question", "kb_require_question")):
            if flag in body:
                if not isinstance(body[flag], bool):
                    return JSONResponse({"ok": False, "error": f"{flag} 应为布尔值"}, status_code=400)
                updates[field] = "true" if body[flag] else "false"
        for key, field, low, high in (("cooldown", "kb_cooldown", 0, 86400),
                                      ("suggest_min", "kb_suggest_min", 1, 50)):
            if key in body:
                try:
                    val = int(body[key])
                except (TypeError, ValueError):
                    return JSONResponse({"ok": False, "error": f"{key} 应为整数"}, status_code=400)
                if not (low <= val <= high):
                    return JSONResponse({"ok": False, "error": f"{key} 应在 {low}-{high} 之间"}, status_code=400)
                updates[field] = str(val)
        if "min_score" in body:
            try:
                val = float(body["min_score"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "min_score 不合法"}, status_code=400)
            updates["kb_min_score"] = str(round(max(0.5, min(val, 20.0)), 2))
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            for key, value in updates.items():
                await store.set_kv(key, value)
        except Exception:
            logger.exception("面板保存知识库配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "知识库配置已保存并生效"}})

    @app.get("/panel/api/recall")
    async def panel_recall_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import recall

        return JSONResponse({
            "ok": True,
            "data": {
                "enabled": await recall.is_enabled(),
                "stats": recall.stats_sync(),
                "max_docs": int(await recall._kv("recall_max_docs")),
                "days": int(await recall._kv("recall_days")),
            },
        })

    @app.post("/panel/api/recall")
    async def panel_recall_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import recall

        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return JSONResponse({"ok": False, "error": "enabled 应为布尔值"}, status_code=400)
            updates["recall_enabled"] = "true" if body["enabled"] else "false"
        for key, field in (("days", "recall_days"), ("max_docs", "recall_max_docs")):
            if key in body:
                try:
                    val = int(body[key])
                except (TypeError, ValueError):
                    return JSONResponse({"ok": False, "error": f"{key} 应为整数"}, status_code=400)
                if not (1 <= val <= 100000):
                    return JSONResponse({"ok": False, "error": f"{key} 应在 1-100000 之间"}, status_code=400)
                updates[field] = str(val)
        if body.get("refresh"):
            added = await recall.refresh()
            if not updates:
                st = recall.stats_sync()
                return JSONResponse({"ok": True, "data": {
                    "message": f"已索引 {added} 条新消息，共 {st['total']} 条",
                    "stats": st,
                }})
        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            for key, value in updates.items():
                await store.set_kv(key, value)
        except Exception:
            logger.exception("面板保存群聊回忆配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "群聊回忆配置已保存并生效"}})

    @app.get("/panel/api/search")
    async def panel_search_get(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import webtools

        chain = await webtools.search_chain()
        cfg = await llm.ai_config()
        return JSONResponse({
            "ok": True,
            "data": {
                "chain": chain,
                "providers": await webtools.provider_status(),
                "daily_limit": int(await webtools._kv("search_daily_limit")),
                "used_today": await webtools._used_today(),
                "cache_ttl": int(await webtools._kv("search_cache_ttl")),
                "need_content": bool(await webtools._kv("search_need_content")),
                "native_search": bool(cfg["ai_native_search"]),
                "native_ready": await llm.native_search_ready(cfg),
            },
        })

    @app.post("/panel/api/search")
    async def panel_search_post(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        from src.plugins import webtools

        body = await request.json()
        store = get_store()
        updates: dict[str, str] = {}

        if "chain" in body:
            chain = webtools._parse_chain(body["chain"])
            if not chain:
                return JSONResponse({"ok": False, "error": "至少保留一个搜索源"}, status_code=400)
            updates["search_providers"] = json.dumps(chain)
        if "daily_limit" in body:
            try:
                limit = int(body["daily_limit"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "每日上限应为整数"}, status_code=400)
            if not (0 <= limit <= 100000):
                return JSONResponse({"ok": False, "error": "每日上限应在 0-100000 之间"}, status_code=400)
            updates["search_daily_limit"] = str(limit)
        if "cache_ttl" in body:
            try:
                ttl = int(body["cache_ttl"])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "缓存时长应为秒数"}, status_code=400)
            updates["search_cache_ttl"] = str(max(0, min(ttl, 86400)))
        for flag, key in (("need_content", "search_need_content"),
                          ("native_search", "ai_native_search")):
            if flag in body:
                if not isinstance(body[flag], bool):
                    return JSONResponse({"ok": False, "error": f"{flag} 应为布尔值"}, status_code=400)
                updates[key] = "true" if body[flag] else "false"

        if "test" in body:
            query = str(body["test"]).strip() or "DeepSeek"
            webtools._reset_state()  # 测试不受缓存影响
            results, note = await webtools.search(query)
            if results is None:
                return JSONResponse({"ok": False, "error": note}, status_code=200)
            return JSONResponse({
                "ok": True,
                "data": {
                    "message": f"搜索成功，返回 {len(results)} 条" if results else "搜索源正常，但没有结果",
                    "sample": webtools.format_results(results[:3]) if results else "",
                },
            })

        if not updates:
            return JSONResponse({"ok": False, "error": "没有可保存的字段"}, status_code=400)
        try:
            for key, value in updates.items():
                await store.set_kv(key, value)
        except Exception:
            logger.exception("面板保存搜索配置失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        return JSONResponse({"ok": True, "data": {"message": "搜索配置已保存并生效"}})

    @app.post("/panel/api/ai/models")
    async def panel_ai_models(request: Request) -> JSONResponse:
        """拉取 OpenAI 兼容服务商支持的模型列表（GET {base}/models）。

        请求体可选 base_url / api_key：用于验证尚未保存的凭据；
        缺省时使用已保存的凭据。
        """
        if not _authorized(request):
            return _unauthorized()

        body: dict = {}
        try:
            if await request.body():
                body = await request.json()
        except Exception:
            body = {}
        if body.get("provider") == "deepseek":
            ds = await llm.deepseek_settings()
            base, key = ds["base_url"], ds["api_key"]
            body = {"api_key": body.get("api_key")}  # base_url 固定为 DeepSeek 官方地址
        else:
            base, key = await llm._ai_creds()
        base = str(body.get("base_url") or base or "").strip().rstrip("/")
        key = str(body.get("api_key") or key or "").strip()
        if not (base and key):
            return JSONResponse(
                {"ok": False, "error": "请先填写 API 地址与密钥（或先保存连接）"}, status_code=400
            )
        if not base.startswith(("http://", "https://")):
            return JSONResponse({"ok": False, "error": "API 地址应以 http(s):// 开头"}, status_code=400)

        try:
            resp = await get_http().get(
                f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15,
            )
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
