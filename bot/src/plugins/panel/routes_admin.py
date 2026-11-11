"""面板 API：超管、群白名单、模块开关、入群申请。"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
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

from .common import (
    _authorized,
    _panel_bot,
    _unauthorized,
    group_names,
)


def register(app) -> None:
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
        if key not in ("reply", "welcome", "link", "vision", "proactive", "blog") or not isinstance(enabled, bool):
            return JSONResponse(
                {"ok": False, "error": "key 应为 reply/welcome/link/vision/proactive/blog，enabled 应为布尔值"}, status_code=400
            )
        kv_key = {
            "link": "link_preview_enabled",
            "vision": "vision_guard_enabled",
            "proactive": "proactive_enabled",
        }.get(key, f"{key}_enabled")
        try:
            await get_store().set_kv(kv_key, "true" if enabled else "false")
        except Exception:
            logger.exception("面板写入模块开关失败")
            return JSONResponse({"ok": False, "error": "写入失败"}, status_code=500)
        if key == "reply":
            reply_plugin.set_enabled(enabled)
        labels = {
            "reply": "关键词模块",
            "welcome": "进群欢迎",
            "link": "链接自动解读",
            "vision": "图片识别与违规处理",
            "proactive": "群聊主动性",
            "blog": "博客知识关联推荐",
        }
        return JSONResponse(
            {"ok": True, "data": {"message": f"{labels[key]}已{'开启' if enabled else '关闭'}"}}
        )

    @app.get("/panel/api/group-names")
    async def panel_group_names(request: Request, refresh: bool = False) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        names = await group_names(force=refresh)
        return JSONResponse({"ok": True, "data": {
            "names": {str(k): v for k, v in names.items()}, "connected": _panel_bot() is not None,
        }})

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
