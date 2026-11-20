"""AI 工具：机器人配置与超管操作（白名单、模块开关、敏感词、欢迎语、超管、AI 设置、申诉等）。"""

from __future__ import annotations

from src import llm
from src.aitools.registry import _ai, _j, tool
from src.config import get_config
from src.store import get_store


@tool(
    "list_whitelist",
    "查看群白名单",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_whitelist_list(bot, event, args) -> str:
    from src.permission import merged_whitelist
    env_ids = get_config().xingchao_group_whitelist
    return _j({"白名单": [{"群号": g, "来源": "环境变量" if g in env_ids else "运行时"} for g in sorted(merged_whitelist())]})


@tool(
    "add_whitelist_group",
    "添加群到白名单",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
    "superuser",
)
async def _t_whitelist_add(bot, event, args) -> str:
    from src.permission import add_runtime_group
    ok = await add_runtime_group(int(args["group_id"]))
    return f"已添加群 {args['group_id']}。" if ok else f"群 {args['group_id']} 已在白名单中。"


@tool(
    "remove_whitelist_group",
    "从白名单移除群（env 来源除外）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
    "superuser",
)
async def _t_whitelist_del(bot, event, args) -> str:
    from src.permission import remove_runtime_group
    gid = int(args["group_id"])
    if gid in get_config().xingchao_group_whitelist:
        return f"群 {gid} 来自环境变量，无法移除，请修改 XINGCHAO_GROUP_WHITELIST。"
    ok = await remove_runtime_group(gid)
    return f"已移除群 {gid}。" if ok else f"群 {gid} 不在运行时白名单中。"


@tool(
    "set_group_business",
    "开启/关闭某群的业务（临时开关）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["group_id", "enabled"]},
    "superuser",
)
async def _t_group_switch(bot, event, args) -> str:
    from src.permission import set_group_enabled
    gid = int(args["group_id"])
    enable = bool(args.get("enabled", True))
    changed = await set_group_enabled(gid, enable)
    state = "开启" if enable else "关闭"
    return f"已{state}群 {gid} 的业务。" if changed else f"群 {gid} 已处于{state}状态。"


@tool(
    "list_replies",
    "查看关键词词库",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_reply_list(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    items = reply_plugin.get_items()
    return _j({"词条数": len(items), "词条": [{"id": it["id"], "触发词": it["pattern"], "匹配": it.get("match", "exact"), "启用": it.get("enabled", True)} for it in items]})


@tool(
    "reload_replies",
    "重载关键词词库",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_reply_reload(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    return f"词库已重载，共 {reply_plugin.reload_replies()} 条词条。"


@tool(
    "set_reply_enabled",
    "开启/关闭关键词回复模块",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_reply_toggle(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    enable = bool(args.get("enabled", True))
    reply_plugin.set_enabled(enable)
    await get_store().set_kv("reply_enabled", "true" if enable else "false")
    return f"关键词模块已{'开启' if enable else '关闭'}。"


@tool(
    "set_link_preview_enabled",
    "开启/关闭链接自动解读功能（群里发链接时 AI 一句话概括）",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_link_preview_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("link_preview_enabled", "true" if enable else "false")
    return f"链接自动解读已{'开启' if enable else '关闭'}。"


@tool(
    "set_vision_guard_enabled",
    "开启/关闭图片 AI 识别与违规图片自动撤回禁言",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_vision_guard_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("vision_guard_enabled", "true" if enable else "false")
    return f"图片 AI 识别与违规处理已{'开启' if enable else '关闭'}。"


@tool(
    "set_vision_model",
    "设置图片识别用的视觉模型（需支持图片输入）",
    {"type": "object", "properties": {"model": {"type": "string", "description": "如 glm-4v-flash"}}, "required": ["model"]},
    "superuser",
)
async def _t_vision_model_set(bot, event, args) -> str:
    model = str(args.get("model", "")).strip()
    if not model:
        return "错误：需要 model（视觉模型名，如 glm-4v-flash）。"
    await get_store().set_kv("ai_vision_model", model)
    return f"视觉模型已设为：{model}"


@tool(
    "set_bot_signature",
    "修改机器人账号的个性签名（最多 60 字，留空清空）",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": []},
    "superuser",
)
async def _t_set_signature(bot, event, args) -> str:
    from src.plugins import interact
    ok, msg = await interact.set_signature(bot, str(args.get("text", "")))
    return msg if ok else f"执行失败：{msg}"


@tool(
    "set_voice_character",
    "切换/查看 QQ AI 语音角色（character 留空列出可选角色；可填序号或名称）",
    {"type": "object", "properties": {"character": {"type": "string"}}, "required": []},
    "superuser",
)
async def _t_voice_character(bot, event, args) -> str:
    from src.plugins import voice
    gid = getattr(event, "group_id", None) or voice._api_group(event)
    return await voice.choose_character(bot, gid, str(args.get("character", "")))


@tool(
    "set_voice_reply",
    "开启/关闭当前会话的默认语音回复（开启后我的回复默认以语音发出；用户说“以后都用语音回复/别发语音了、用文字”时使用）",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_voice_reply_toggle(bot, event, args) -> str:
    from src.plugins.voice import set_voice_reply

    enabled = bool(args.get("enabled"))
    await set_voice_reply(event, enabled)
    return "已开启：之后我在当前会话默认用语音回复" if enabled else "已关闭：之后改回文字回复"


@tool(
    "run_capability_check",
    "探测当前 QQ 连接（NapCat）支持哪些能力（精华、收藏表情包、OCR、历史消息等，只读不改动；用户问“你现在有哪些 QQ 能力/支持精华吗”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_capability_check(bot, event, args) -> str:
    from src.permission import merged_whitelist
    from src.plugins import capabilities
    gid = getattr(event, "group_id", None) or next(iter(sorted(merged_whitelist())), None)
    if not gid:
        return "错误：白名单为空，没有可用于探测的群。"
    return await capabilities.run_and_store(bot, int(gid))


@tool(
    "set_proactive_mode",
    "设置机器人在群里的主动程度档位：安静（很少插话）/ 正常 / 话痨（更爱接话、评论图片、复读）；默认设置当前群，group_id 填“全局”则改全局默认（用户说“话痨一点/安静点/少说话”时使用）",
    {"type": "object", "properties": {"mode": {"type": "string", "enum": ["安静", "正常", "话痨"]}, "group_id": {"type": "string", "description": "群号或“全局”，默认当前群"}}, "required": ["mode"]},
    "superuser",
)
async def _t_proactive_mode(bot, event, args) -> str:
    from src.permission import merged_whitelist
    from src.plugins import proactive

    mode = proactive.MODE_ALIASES.get(str(args.get("mode", "")).strip().lower())
    if mode is None:
        return "错误：mode 只能是 安静 / 正常 / 话痨（quiet/normal/chatty）。"
    scope = args.get("group_id")
    if scope in (None, "", 0):
        scope = getattr(event, "group_id", None)
    if str(scope) in ("global", "全局"):
        await proactive.set_mode(mode)
        return f"全局默认主动程度已设为「{proactive.MODE_LABEL[mode]}」。"
    if not scope:
        return "错误：私聊中请提供 group_id，或说“全局”。"
    if int(scope) not in merged_whitelist():
        return f"错误：群 {scope} 不在白名单内。"
    await proactive.set_mode(mode, int(scope))
    return f"群 {scope} 的主动程度已设为「{proactive.MODE_LABEL[mode]}」。"


@tool(
    "set_proactive_enabled",
    "开启/关闭群聊主动性（话题明确或有人求助时 AI 自动参与讨论）",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_proactive_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("proactive_enabled", "true" if enable else "false")
    return f"群聊主动性已{'开启' if enable else '关闭'}。"


@tool(
    "list_sensitive_words",
    "查看敏感词词库（命中即撤回，可选禁言）与监控开关状态",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_sensitive_list(bot, event, args) -> str:
    from src.plugins import sensitive as sp

    cfg = await sp.sensitive_config()
    words = await sp.get_words()
    return _j({
        "监控开关": "开启" if cfg["sensitive_enabled"] else "关闭",
        "词数": len(words),
        "词库": words,
    })


@tool(
    "add_sensitive_words",
    "批量添加敏感词（全局词库，命中后自动撤回/禁言；用户说“把 xxx 加进敏感词/违禁词”时使用）",
    {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}, "description": "要添加的敏感词列表，可一次多个"}}, "required": ["words"]},
    "superuser",
)
async def _t_sensitive_add(bot, event, args) -> str:
    from src.plugins import sensitive as sp

    words = [str(w) for w in (args.get("words") or []) if str(w).strip()]
    if not words:
        return "错误：缺少 words（要添加的敏感词列表）。"
    try:
        added, existing = await sp.add_words(words)
    except ValueError as e:
        return f"错误：{e}"
    cfg = await sp.sensitive_config()
    parts = [f"新增 {len(added)} 个：{'、'.join(added) if added else '无'}"]
    if existing:
        parts.append(f"已存在 {len(existing)} 个：{'、'.join(existing)}")
    parts.append(f"当前词库共 {len(await sp.get_words())} 个")
    if added and not cfg["sensitive_enabled"]:
        parts.append("注意：敏感词监控当前处于关闭状态，词库暂不生效，需在面板开启")
    return "；".join(parts) + "。"


@tool(
    "remove_sensitive_words",
    "批量移除敏感词（全局词库）",
    {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}, "description": "要移除的敏感词列表，可一次多个"}}, "required": ["words"]},
    "superuser",
)
async def _t_sensitive_remove(bot, event, args) -> str:
    from src.plugins import sensitive as sp

    words = [str(w) for w in (args.get("words") or []) if str(w).strip()]
    if not words:
        return "错误：缺少 words（要移除的敏感词列表）。"
    removed, missing = await sp.remove_words(words)
    parts = [f"已移除 {len(removed)} 个：{'、'.join(removed) if removed else '无'}"]
    if missing:
        parts.append(f"不在词库中 {len(missing)} 个：{'、'.join(missing)}")
    parts.append(f"当前词库共 {len(await sp.get_words())} 个")
    return "；".join(parts) + "。"


@tool(
    "get_welcome",
    "查看进群欢迎语及开关状态",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_welcome_view(bot, event, args) -> str:
    from src.plugins import groupadmin as ga
    return _j({"启用": await ga.is_welcome_enabled(), "欢迎语": await ga.get_welcome_text()})


@tool(
    "set_welcome",
    "设置进群欢迎语（支持占位符 {at}=@新人、{qq}=新人QQ、{group}=群号）",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    "superuser",
)
async def _t_welcome_set(bot, event, args) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "错误：需要 text（欢迎语内容，支持占位符 {at}/{qq}/{group}）。"
    if len(text) > 1000:
        return "错误：欢迎语过长（上限 1000 字）。"
    await get_store().set_kv("welcome_text", text)
    return f"欢迎语已更新：{text[:150]}"


@tool(
    "set_welcome_enabled",
    "开启/关闭新人进群欢迎",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_welcome_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("welcome_enabled", "true" if enable else "false")
    return f"新人进群欢迎已{'开启' if enable else '关闭'}。"


@tool(
    "set_ad_policy",
    "修改广告招商政策（招商说明/报价/联系人/开关/命中处理/reply 静默 log/合作方增删）",
    {"type": "object", "properties": {
        "enabled": {"type": "boolean", "description": "广告招商功能开关"},
        "text": {"type": "string", "description": "招商说明正文"},
        "price": {"type": "string", "description": "报价单正文"},
        "contact": {"type": "string", "description": "广告对接人 QQ 号"},
        "action": {"type": "string", "enum": ["reply", "silent", "log"], "description": "群里检测到广告时的处理：reply=撤回并提示 / silent=只撤回 / log=只通知超管"},
        "partner_add": {"type": "integer", "description": "加入广告合作方的 QQ（其发言不会被当广告撤回）"},
        "partner_remove": {"type": "integer", "description": "移出广告合作方的 QQ"},
    }, "required": []},
    "superuser",
)
async def _t_ad_set(bot, event, args) -> str:
    from src.plugins import ads as ads_plugin

    updates: list[str] = []
    store = get_store()
    if "enabled" in args and args["enabled"] is not None:
        enable = bool(args["enabled"])
        await store.set_kv("ad_enabled", "true" if enable else "false")
        updates.append(f"招商{'开启' if enable else '关闭'}")
    if args.get("text"):
        text = str(args["text"]).strip()
        if len(text) > ads_plugin.MAX_AD_TEXT:
            return f"错误：招商说明过长（上限 {ads_plugin.MAX_AD_TEXT} 字）。"
        await store.set_kv("ad_text", text)
        updates.append("招商说明已更新")
    if args.get("price"):
        price = str(args["price"]).strip()
        if len(price) > ads_plugin.MAX_AD_PRICE:
            return f"错误：报价过长（上限 {ads_plugin.MAX_AD_PRICE} 字）。"
        await store.set_kv("ad_price", price)
        updates.append("报价已更新")
    if args.get("contact"):
        contact = str(args["contact"]).strip().lstrip("@")
        if not contact.isdigit():
            return "错误：contact 应为 QQ 号。"
        await store.set_kv("ad_contact", contact)
        updates.append(f"联系人设为 {contact}")
    if args.get("action"):
        key = ads_plugin._ACTION_ALIASES.get(str(args["action"]).strip().lower())
        key = key or ads_plugin._ACTION_ALIASES.get(str(args["action"]).strip())
        if not key:
            return "错误：action 只能是 reply / silent / log。"
        await store.set_kv("ad_action", key)
        updates.append(f"命中处理设为{ads_plugin.ACTION_LABEL[key]}")
    if args.get("partner_add"):
        partners = await ads_plugin.ad_partners()
        uid = int(args["partner_add"])
        if uid not in partners:
            partners.append(uid)
            await ads_plugin.save_partners(partners)
            updates.append(f"{uid} 加入合作方")
    if args.get("partner_remove"):
        partners = await ads_plugin.ad_partners()
        uid = int(args["partner_remove"])
        if uid in partners:
            partners.remove(uid)
            await ads_plugin.save_partners(partners)
            updates.append(f"{uid} 移出合作方")
    if not updates:
        return "错误：没有可修改的字段。"
    return "；".join(updates) + "。"


@tool(
    "run_backup",
    "立即备份数据库并列出现有备份（用户说“备份一下数据”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_run_backup(bot, event, args) -> str:
    from src.plugins.backup import backup_dir, run_backup
    try:
        path, removed = await run_backup()
    except Exception as e:
        return _j({"ok": False, "错误": f"备份失败：{e}"})
    files = sorted(backup_dir().glob("xingchao-*.db.gz"), reverse=True)
    return _j({"ok": True, "本次备份": path.name, "大小KB": path.stat().st_size // 1024,
               "清理旧备份": removed, "现有份数": len(files), "最近备份": [f.name for f in files[:8]]})


@tool(
    "list_superusers",
    "查看超级管理员列表",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_superuser_list(bot, event, args) -> str:
    from src.permission import runtime_superuser_ids, superuser_ids
    env_ids = get_config().xingchao_superusers
    return _j({"超管": [{"QQ": u, "来源": "env" if u in env_ids else "运行时"}
                      for u in sorted(superuser_ids() | runtime_superuser_ids())]})


@tool(
    "add_superuser",
    "添加超级管理员",
    {"type": "object", "properties": {"user_id": {"type": "integer", "description": "QQ 号"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_superuser_add(bot, event, args) -> str:
    from src.permission import add_runtime_superuser
    uid = int(args["user_id"])
    return f"已添加超管 {uid}。" if await add_runtime_superuser(uid) else f"QQ {uid} 已是超管。"


@tool(
    "remove_superuser",
    "移除超级管理员（env 来源除外）",
    {"type": "object", "properties": {"user_id": {"type": "integer", "description": "QQ 号"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_superuser_del(bot, event, args) -> str:
    from src.permission import remove_runtime_superuser
    uid = int(args["user_id"])
    if uid in get_config().xingchao_superusers:
        return f"QQ {uid} 来自环境变量，无法移除，请修改 XINGCHAO_SUPERUSERS。"
    return f"已移除超管 {uid}。" if await remove_runtime_superuser(uid) else f"QQ {uid} 不在运行时超管中。"


@tool(
    "set_ai_enabled",
    "开启/关闭 AI 问答功能",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_ai_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    if not (await llm.is_configured()):
        return "AI 未配置 API 地址或密钥，请先在 Web 管理面板「AI」页填写。"
    await get_store().set_kv("ai_enabled", "true" if enable else "false")
    return f"AI 问答已{'开启' if enable else '关闭'}。"


@tool(
    "get_ai_status",
    "查看 AI 问答状态、模型、限额与今日用量",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_ai_status(bot, event, args) -> str:
    cfg = await llm.ai_config()
    return _j({
        "开启": await llm.is_ai_enabled(), "已配置API": await llm.is_configured(),
        "模型": cfg["ai_model"], "视觉模型": await llm._vision_model(),
        "上下文轮数": cfg["ai_ctx_rounds"],
        "今日限额": {"每群": cfg["ai_limit_group"], "每人": cfg["ai_limit_user"]},
        "今日用量": await _ai()._usage_payload(),
    })


@tool(
    "clear_ai_context",
    "清空本群/本会话的 AI 对话上下文",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_ai_clear(bot, event, args) -> str:
    group_key = getattr(event, "group_id", 0) or -int(event.user_id)
    _ai()._ctx.pop(group_key, None)
    return "本会话 AI 上下文已清空。"


@tool(
    "list_appeals",
    "查看待处理的违规申诉（编号、申诉人、被处罚原文、申诉理由）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_appeal_list(bot, event, args) -> str:
    from src.plugins import appeals
    pending = await get_store().pending_appeals()
    if not pending:
        return "当前没有待处理的违规申诉。"
    items = []
    for a in pending:
        rec = await get_store().get_punishment(a["id"])
        items.append({
            "编号": a["id"], "QQ": a["user_id"], "群号": a["group_id"], "命中": a["word"],
            "处罚": appeals.action_desc(rec) if rec else a["action"],
            "原文": await appeals.original_block(rec) if rec else "",
            "申诉理由": a["appeal_text"], "申诉时间": a["appeal_ts"],
        })
    return _j({"待处理申诉": items, "提示": "用 resolve_appeal 通过/驳回；通过会自动解除禁言并通知申诉人"})


@tool(
    "resolve_appeal",
    "通过或驳回违规申诉（通过会自动解除禁言并私聊通知申诉人；先用 list_appeals 获取编号）",
    {"type": "object", "properties": {"appeal_id": {"type": "integer", "description": "申诉编号"}, "approve": {"type": "boolean", "description": "true=通过，false=驳回"}, "note": {"type": "string", "description": "给申诉人的备注/驳回理由（可空）"}}, "required": ["appeal_id", "approve"]},
    "superuser",
)
async def _t_appeal_resolve(bot, event, args) -> str:
    from src.plugins import appeals
    pid, approve = args.get("appeal_id"), args.get("approve")
    if not isinstance(pid, int) or not isinstance(approve, bool):
        return "错误：需要 appeal_id（整数，可先用 list_appeals 查看）与 approve（布尔值）。"
    ok, msg = await appeals.resolve_appeal(
        bot, pid, approve, str(args.get("note", "")).strip()[:200],
        operator=f"QQ {event.user_id} (AI)", operator_uid=event.user_id,
    )
    return msg if ok else f"错误：{msg}"


@tool(
    "refresh_blog",
    "刷新博客知识库（重新抓取 xuyi.dev 的最新文章列表）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_blog_refresh(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    result = await blog_plugin.refresh(force=True)
    if result.get("ok"):
        return f"博客知识库已刷新：{result['count']} 篇文章（{result.get('time', '')}）。"
    return f"刷新失败：{result.get('error', '未知错误')}（沿用旧数据，稍后会自动重试）。"


@tool(
    "set_blog_enabled",
    "开启/关闭博客知识关联推荐（关闭后 AI 不再优先推荐博客内容）",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    "superuser",
)
async def _t_blog_toggle(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    enabled = bool(args.get("enabled"))
    await blog_plugin.set_enabled(enabled)
    return f"博客知识关联推荐已{'开启' if enabled else '关闭'}。"
