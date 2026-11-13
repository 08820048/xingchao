"""AI 工具注册表：LLM 可调用的全部工具（OpenAI function-calling 定义 + 本地执行器）。

从 plugins/ai.py 拆出（ai.py 只保留 LLM 客户端、上下文、对话循环和消息入口）。
- 每个工具：schema、perm（all=所有人 / superuser=仅超管）、handler(bot, event, args) -> str
- 新增工具：写一个 `_t_xxx` 处理函数，再在 build_tools() 里登记；同时更新 docs/AI_CAPABILITIES.md
- 本模块不是 NoneBot 插件（不在 plugins/ 目录下，不注册任何匹配器）；
  需要 ai.py 里的配置/客户端函数时用 _ai() 延迟导入，避免循环依赖
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from src.config import get_config
from src.store import get_store


def _ai():
    from src.plugins import ai

    return ai


# ---------------------------------------------------------------- 工具注册表
# 每个工具：schema（OpenAI function 定义）、perm（all=所有人 / superuser=仅超管）、
# handler(bot, event, args) -> str（给 LLM 的结果摘要，中文）


def _tool(name: str, description: str, params: dict, perm: str, handler) -> tuple[dict, Any]:
    """返回 (OpenAI schema, 执行器)。schema 传给 SDK，执行器本地保存。"""
    schema = {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }
    return schema, (perm, handler)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


async def _t_group_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    info = await bot.call_api("get_group_info", group_id=int(group_id))
    return _j({"群号": info["group_id"], "群名": info.get("group_name"), "成员数": info.get("member_count"), "上限": info.get("max_member_count")})


async def _t_member_list(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号。"
    members = await bot.call_api("get_group_member_list", group_id=int(group_id))
    brief = [
        {"QQ": m["user_id"], "昵称": m.get("nickname"), "名片": m.get("card"), "角色": m.get("role")}
        for m in members[:30]
    ]
    return _j({"总数": len(members), "成员（最多30）": brief})


async def _t_member_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not group_id or not uid:
        return "错误：需要群号与用户 QQ。"
    m = await bot.call_api("get_group_member_info", group_id=int(group_id), user_id=int(uid))
    return _j({"QQ": m["user_id"], "昵称": m.get("nickname"), "群名片": m.get("card"), "角色": m.get("role"), "入群时间戳": m.get("join_time"), "等级": m.get("level")})


async def _t_mute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    minutes = int(args.get("duration_minutes", 10))
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=max(60, min(minutes * 60, _ai().MAX_BAN_SECONDS)))
    return f"已禁言 {args['user_id']} {minutes} 分钟。"


async def _t_unmute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=0)
    return f"已解除 {args['user_id']} 的禁言。"


async def _t_kick(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_kick", group_id=int(group_id), user_id=int(args["user_id"]))
    return f"已将 {args['user_id']} 移出群聊。"


async def _t_whole_ban(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    enable = bool(args.get("enable", True))
    await bot.call_api("set_group_whole_ban", group_id=int(group_id), enable=enable)
    return "已开启全体禁言。" if enable else "已关闭全体禁言。"


async def _t_stats(bot, event, args) -> str:
    from src.plugins.ai import _usage_payload  # noqa: F401  （保持模块一致）
    store = get_store()
    day = args.get("day") or datetime.now().astimezone().strftime("%Y-%m-%d")
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if group_id:
        total, users = await store.get_group_day_stat(int(group_id), day)
        top = await store.get_top_users(int(group_id), day)
        return _j({"日期": day, "群号": int(group_id), "消息总数": total, "参与人数": users, "Top5": [{"QQ": u, "条数": c} for u, c in top]})
    overview = await store.get_day_overview(day)
    return _j({"日期": day, "各群": [{"群号": g, "消息数": c} for g, c in overview]})


async def _t_whitelist_list(bot, event, args) -> str:
    from src.permission import merged_whitelist
    env_ids = get_config().xingchao_group_whitelist
    return _j({"白名单": [{"群号": g, "来源": "环境变量" if g in env_ids else "运行时"} for g in sorted(merged_whitelist())]})


async def _t_whitelist_add(bot, event, args) -> str:
    from src.permission import add_runtime_group
    ok = await add_runtime_group(int(args["group_id"]))
    return f"已添加群 {args['group_id']}。" if ok else f"群 {args['group_id']} 已在白名单中。"


async def _t_whitelist_del(bot, event, args) -> str:
    from src.permission import remove_runtime_group
    gid = int(args["group_id"])
    if gid in get_config().xingchao_group_whitelist:
        return f"群 {gid} 来自环境变量，无法移除，请修改 XINGCHAO_GROUP_WHITELIST。"
    ok = await remove_runtime_group(gid)
    return f"已移除群 {gid}。" if ok else f"群 {gid} 不在运行时白名单中。"


async def _t_group_switch(bot, event, args) -> str:
    from src.permission import set_group_enabled
    gid = int(args["group_id"])
    enable = bool(args.get("enabled", True))
    changed = await set_group_enabled(gid, enable)
    state = "开启" if enable else "关闭"
    return f"已{state}群 {gid} 的业务。" if changed else f"群 {gid} 已处于{state}状态。"


async def _t_reply_list(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    items = reply_plugin.get_items()
    return _j({"词条数": len(items), "词条": [{"id": it["id"], "触发词": it["pattern"], "匹配": it.get("match", "exact"), "启用": it.get("enabled", True)} for it in items]})


async def _t_reply_reload(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    return f"词库已重载，共 {reply_plugin.reload_replies()} 条词条。"


async def _t_gh_trending(bot, event, args) -> str:
    from src.plugins import github as gh
    since = str(args.get("since", "daily"))
    language = str(args.get("language", ""))
    try:
        items = await gh.fetch_trending(since, language)
        return _j({"趋势榜": items})
    except Exception as e:
        return f"获取趋势失败：{e}"


async def _t_pending_requests(bot, event, args) -> str:
    from src.plugins import request as req
    if not req._PENDING:
        return "当前没有待审批的入群申请。注意：只有机器人运行期间转人工的申请才会进入此列表；QQ 客户端里直接收到的申请需要在 QQ 群管理设置中处理。"
    return _j({"待审批": [
        {"序号": seq, "群号": r["group_id"], "QQ": r["user_id"], "回答": r["comment"] or "（无）"}
        for seq, r in sorted(req._PENDING.items())
    ]})


async def _t_approve_request(bot, event, args) -> str:
    from src.plugins import request as req
    try:
        seq = int(args["seq"])
    except (KeyError, TypeError, ValueError):
        return "错误：需要 seq（申请序号，可先用查看工具获取）。"
    r = req._PENDING.get(seq)
    if not r:
        return f"没有找到申请 #{seq}（可能已处理、过期或重启丢失）。"
    await bot.call_api("set_group_add_request", flag=r["flag"],
                       sub_type=r["sub_type"], approve=True, reason="")
    req._PENDING.pop(seq)
    return f"已通过申请 #{seq}（QQ {r['user_id']}）。"


async def _t_reject_request(bot, event, args) -> str:
    from src.plugins import request as req
    try:
        seq = int(args["seq"])
    except (KeyError, TypeError, ValueError):
        return "错误：需要 seq（申请序号）。"
    reason = str(args.get("reason", "不符合入群要求"))
    r = req._PENDING.get(seq)
    if not r:
        return f"没有找到申请 #{seq}（可能已处理、过期或重启丢失）。"
    await bot.call_api("set_group_add_request", flag=r["flag"],
                       sub_type=r["sub_type"], approve=False, reason=reason)
    req._PENDING.pop(seq)
    return f"已拒绝申请 #{seq}（QQ {r['user_id']}），理由：{reason}"


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


async def _t_recall(bot, event, args) -> str:
    message_id = args.get("message_id")
    if not message_id and getattr(event, "reply", None) is not None:
        message_id = event.reply.message_id
    if not message_id:
        return "错误：需要 message_id，或回复（引用）目标消息后再让我撤回。"
    await bot.call_api("delete_msg", message_id=int(message_id))
    return f"已撤回消息 {message_id}。"


async def _t_ping(bot, event, args) -> str:
    return _j({"连通": "pong", "self_id": bot.self_id})


async def _t_my_id(bot, event, args) -> str:
    gid = getattr(event, "group_id", None)
    return _j({"group_id": gid, "user_id": event.user_id, "self_id": bot.self_id, "环境": "群聊" if gid else "私聊"})


async def _t_help(bot, event, args) -> str:
    from src import helpmenu
    from src.plugins.basic import _is_group_admin
    return helpmenu.render_menu(await _is_group_admin(bot, event))


async def _t_about(bot, event, args) -> str:
    cfg = get_config()
    return _j({
        "机器人": "星潮（开源 QQ 群助手机器人）",
        "开发者": "XuYi",
        "开发者QQ": cfg.xingchao_developer_id,
        "开发者博客": cfg.xingchao_developer_blog,
        "官网": cfg.xingchao_developer_site,
        "开源地址": "https://github.com/08820048/xingchao",
    })


async def _t_blog_search(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    query = str(args.get("keywords", "")).strip()
    if not query:
        return "错误：需要 keywords（要搜索的主题关键词，如 Mac 剪贴板、git 分支、端口占用）。"
    if not await blog_plugin.is_enabled():
        return "博客知识关联推荐当前已关闭（超管可开启）。"
    hits = await blog_plugin.search(query, limit=5)
    if not hits:
        return "博客知识库中没有找到与该主题相关的内容（可能尚未就绪，或确实没有相关文章/作品）。"
    return _j({"结果（优先级由高到低）": hits})


async def _t_blog_post(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    target = str(args.get("url_or_title", "")).strip()
    if not target:
        return "错误：需要 url_or_title（文章链接或标题关键词）。"
    post = await blog_plugin.get_post(target)
    if not post:
        return "错误：未找到该文章，可先用 search_blog 搜索主题。"
    return _j({
        "标题": post.get("title"), "分类": post.get("category"),
        "发布日期": post.get("published"), "链接": post.get("url"),
        "摘要": post.get("summary"), "正文节选": post.get("excerpt"),
    })


async def _t_blog_refresh(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    result = await blog_plugin.refresh(force=True)
    if result.get("ok"):
        return f"博客知识库已刷新：{result['count']} 篇文章（{result.get('time', '')}）。"
    return f"刷新失败：{result.get('error', '未知错误')}（沿用旧数据，稍后会自动重试）。"


async def _t_blog_toggle(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    enabled = bool(args.get("enabled"))
    await blog_plugin.set_enabled(enabled)
    return f"博客知识关联推荐已{'开启' if enabled else '关闭'}。"


async def _t_welcome_view(bot, event, args) -> str:
    from src.plugins import groupadmin as ga
    return _j({"启用": await ga.is_welcome_enabled(), "欢迎语": await ga.get_welcome_text()})


async def _t_welcome_set(bot, event, args) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "错误：需要 text（欢迎语内容，支持占位符 {at}/{qq}/{group}）。"
    if len(text) > 1000:
        return "错误：欢迎语过长（上限 1000 字）。"
    await get_store().set_kv("welcome_text", text)
    return f"欢迎语已更新：{text[:150]}"


async def _t_welcome_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("welcome_enabled", "true" if enable else "false")
    return f"新人进群欢迎已{'开启' if enable else '关闭'}。"


async def _t_notice_publish(bot, event, args) -> str:
    content = str(args.get("content", "")).strip()
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not content:
        return "错误：需要 content（公告内容）。"
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    try:
        await bot.call_api("_send_group_notice", group_id=int(group_id), content=content)
    except Exception:
        await bot.call_api("send_group_notice", group_id=int(group_id), content=content)
    return "群公告已发布。"


async def _t_notice_list(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    notices = await bot.call_api("get_group_notice", group_id=int(group_id))
    items = notices if isinstance(notices, list) else notices.get("notices", [])
    return _j({"公告": [{"内容": str(n.get("content", ""))[:100], "发布时间": n.get("publish_time", "")} for n in items[:5]]})


async def _t_group_send(bot, event, args) -> str:
    """超管让机器人在指定群发一条普通消息（仅限白名单群，避免误发到不受控的群）。"""
    from src.permission import merged_whitelist

    text = str(args.get("text", "")).strip()
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not text:
        return "错误：需要 text（要发送的内容）。"
    if not group_id:
        return "错误：需要 group_id（目标群号）；私聊时必须指定，群聊中默认当前群。"
    group_id = int(group_id)
    if group_id not in merged_whitelist():
        return f"错误：群 {group_id} 不在白名单内，为安全起见不向其发送消息。"
    if len(text) > 500:
        return "错误：内容过长（上限 500 字）。"
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=text)
    except Exception as e:
        return f"发送失败：{e}（常见原因：机器人不在该群、被禁言或非管理员）"
    return f"已发送到群 {group_id}：{text[:60]}"


async def _t_changelog(bot, event, args) -> str:
    from src.plugins.updates import format_entry, load_changelog
    entries = load_changelog()
    if not entries:
        return _j({"更新记录": "暂无"})
    return _j({"最近一次更新": format_entry(entries[0])})


async def _t_run_backup(bot, event, args) -> str:
    from src.plugins.backup import backup_dir, run_backup
    try:
        path, removed = await run_backup()
    except Exception as e:
        return _j({"ok": False, "错误": f"备份失败：{e}"})
    files = sorted(backup_dir().glob("xingchao-*.db.gz"), reverse=True)
    return _j({"ok": True, "本次备份": path.name, "大小KB": path.stat().st_size // 1024,
               "清理旧备份": removed, "现有份数": len(files), "最近备份": [f.name for f in files[:8]]})


async def _t_feed_list(bot, event, args) -> str:
    from src.plugins.feeds import REPEAT_LABEL, SOURCES, WEEKDAY_NAME
    items = []
    for s in await get_store().list_feed_subs():
        rep = REPEAT_LABEL.get(s["repeat"], s["repeat"])
        if s["repeat"] == "weekly" and s["weekday"] is not None:
            rep += f"（{WEEKDAY_NAME[s['weekday']]}）"
        items.append({"id": s["id"], "时间": s["time"], "重复": rep, "内容源": SOURCES.get(s["source"], (s["source"],))[0],
                      "群号": s["group_id"], "启用": bool(s["enabled"])})
    return _j({"内容推送订阅": items, "提示": "增删改请到 Web 面板「内容推送」页"})


async def _t_feed_now(bot, event, args) -> str:
    from src.plugins.feeds import SOURCES, run_sub
    sid = int(args["id"])
    sub = next((s for s in await get_store().list_feed_subs() if s["id"] == sid), None)
    if sub is None or sub["source"] not in SOURCES:
        return _j({"ok": False, "错误": "订阅不存在"})
    ok, msg = await run_sub(bot, sub)
    return _j({"ok": ok, "结果": msg})


async def _t_task_list(bot, event, args) -> str:
    from src.plugins.scheduler import REPEAT_LABEL, WEEKDAY_NAME
    tasks = await get_store().list_tasks()
    items = []
    for t in tasks:
        desc = REPEAT_LABEL.get(t["repeat"], t["repeat"])
        if t["repeat"] == "weekly" and t.get("weekday") is not None:
            desc += f"（{WEEKDAY_NAME[t['weekday']]}）"
        if t["repeat"] == "once" and t.get("date"):
            desc += f"（{t['date']}）"
        items.append({"id": t["id"], "时间": t["time"], "重复": desc, "群号": t["group_id"],
                      "内容": t["message"], "at全体": t["at_all"], "启用": t["enabled"]})
    return _j({"定时任务": items, "提示": "可直接用对话创建/删除/启停，也可在 Web 面板「定时任务」页管理"})


async def _t_task_create(bot, event, args) -> str:
    """创建定时任务（校验规则与面板一致）。"""
    from src.permission import merged_whitelist
    from src.plugins.scheduler import REPEAT_LABEL, WEEKDAY_NAME

    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：需要 group_id（目标群号）；群聊中默认当前群。"
    group_id = int(group_id)
    if group_id not in merged_whitelist():
        return f"错误：群 {group_id} 不在白名单内。"
    time_ = str(args.get("time", "")).strip()
    if not re.fullmatch(r"\d{2}:\d{2}", time_) or time_ >= "24:00":
        return "错误：time 应为 HH:MM（24 小时制，如 09:00）。"
    message = str(args.get("message", "")).strip()
    if not message:
        return "错误：需要 message（要发送的内容）。"
    if len(message) > 2000:
        return "错误：内容过长（上限 2000 字）。"
    repeat = str(args.get("repeat", "daily")).strip()
    if repeat not in ("daily", "weekdays", "weekend", "weekly", "once"):
        return "错误：repeat 应为 daily/weekdays/weekend/weekly/once。"
    weekday = args.get("weekday")
    date_ = args.get("date")
    if repeat == "weekly" and not (isinstance(weekday, int) and 0 <= weekday <= 6):
        return "错误：weekly 需要 weekday（0=周一 … 6=周日）。"
    if repeat == "once" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_ or "")):
        return "错误：once 需要 date（YYYY-MM-DD）。"
    at_all = bool(args.get("at_all", False))
    tid = await get_store().add_task(
        group_id, time_, message, at_all, repeat,
        int(weekday) if repeat == "weekly" else None,
        str(date_) if repeat == "once" else None,
    )
    desc = REPEAT_LABEL.get(repeat, repeat)
    if repeat == "weekly":
        desc = f"每周{WEEKDAY_NAME[int(weekday)][1:]}"
    elif repeat == "once":
        desc = f"仅 {date_} 一次"
    extra = "，并@全体" if at_all else ""
    return f"已创建定时任务 #{tid}：{desc} {time_} → 群 {group_id}{extra}，内容：{message[:40]}"


async def _t_task_delete(bot, event, args) -> str:
    tid = args.get("task_id")
    if not isinstance(tid, int):
        return "错误：需要 task_id（可用 list_scheduled_tasks 查看编号）。"
    ok = await get_store().delete_task(int(tid))
    return f"定时任务 #{tid} 已删除。" if ok else f"错误：任务 #{tid} 不存在。"


async def _t_task_toggle(bot, event, args) -> str:
    tid = args.get("task_id")
    enabled = args.get("enabled")
    if not isinstance(tid, int) or not isinstance(enabled, bool):
        return "错误：需要 task_id（整数）与 enabled（布尔值）。"
    tasks = await get_store().list_tasks()
    if not any(t["id"] == tid for t in tasks):
        return f"错误：任务 #{tid} 不存在。"
    await get_store().update_task(int(tid), enabled=enabled)
    return f"定时任务 #{tid} 已{'启用' if enabled else '停用'}。"


async def _t_superuser_list(bot, event, args) -> str:
    from src.permission import runtime_superuser_ids, superuser_ids
    env_ids = get_config().xingchao_superusers
    return _j({"超管": [{"QQ": u, "来源": "env" if u in env_ids else "运行时"}
                      for u in sorted(superuser_ids() | runtime_superuser_ids())]})


async def _t_superuser_add(bot, event, args) -> str:
    from src.permission import add_runtime_superuser
    uid = int(args["user_id"])
    return f"已添加超管 {uid}。" if await add_runtime_superuser(uid) else f"QQ {uid} 已是超管。"


async def _t_superuser_del(bot, event, args) -> str:
    from src.permission import remove_runtime_superuser
    uid = int(args["user_id"])
    if uid in get_config().xingchao_superusers:
        return f"QQ {uid} 来自环境变量，无法移除，请修改 XINGCHAO_SUPERUSERS。"
    return f"已移除超管 {uid}。" if await remove_runtime_superuser(uid) else f"QQ {uid} 不在运行时超管中。"


async def _t_reply_toggle(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    enable = bool(args.get("enabled", True))
    reply_plugin.set_enabled(enable)
    await get_store().set_kv("reply_enabled", "true" if enable else "false")
    return f"关键词模块已{'开启' if enable else '关闭'}。"


async def _t_ai_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    if not (await _ai().is_configured()):
        return "AI 未配置 API 地址或密钥，请先在 Web 管理面板「AI」页填写。"
    await get_store().set_kv("ai_enabled", "true" if enable else "false")
    return f"AI 问答已{'开启' if enable else '关闭'}。"


async def _t_ai_status(bot, event, args) -> str:
    cfg = await _ai().ai_config()
    return _j({
        "开启": await _ai().is_ai_enabled(), "已配置API": await _ai().is_configured(),
        "模型": cfg["ai_model"], "视觉模型": await _ai()._vision_model(),
        "上下文轮数": cfg["ai_ctx_rounds"],
        "今日限额": {"每群": cfg["ai_limit_group"], "每人": cfg["ai_limit_user"]},
        "今日用量": await _ai()._usage_payload(),
    })


async def _t_link_preview_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("link_preview_enabled", "true" if enable else "false")
    return f"链接自动解读已{'开启' if enable else '关闭'}。"


async def _t_vision_guard_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("vision_guard_enabled", "true" if enable else "false")
    return f"图片 AI 识别与违规处理已{'开启' if enable else '关闭'}。"


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


async def _t_capability_check(bot, event, args) -> str:
    from src.permission import merged_whitelist
    from src.plugins import capabilities
    gid = getattr(event, "group_id", None) or next(iter(sorted(merged_whitelist())), None)
    if not gid:
        return "错误：白名单为空，没有可用于探测的群。"
    return await capabilities.run_and_store(bot, int(gid))


async def _t_memory_get(bot, event, args) -> str:
    from src.plugins import memory
    gid = event.group_id if isinstance(event, GroupMessageEvent) else None
    return await memory.describe_memory(event.user_id, gid)


async def _t_memory_forget(bot, event, args) -> str:
    store = get_store()
    removed = await store.delete_user_memories(event.user_id)
    await store.set_memory_optout(event.user_id, True)
    return f"已清除对该用户的全部印象（{removed} 个群），并停止记录其发言；对方可说“记住我”恢复。"


async def _t_memory_resume(bot, event, args) -> str:
    await get_store().set_memory_optout(event.user_id, False)
    return "已恢复记录，之后会重新慢慢了解对方。"


async def _t_proactive_toggle(bot, event, args) -> str:
    enable = bool(args.get("enabled", True))
    await get_store().set_kv("proactive_enabled", "true" if enable else "false")
    return f"群聊主动性已{'开启' if enable else '关闭'}。"


async def _t_vision_model_set(bot, event, args) -> str:
    model = str(args.get("model", "")).strip()
    if not model:
        return "错误：需要 model（视觉模型名，如 glm-4v-flash）。"
    await get_store().set_kv("ai_vision_model", model)
    return f"视觉模型已设为：{model}"


async def _t_ai_clear(bot, event, args) -> str:
    group_key = getattr(event, "group_id", 0) or -int(event.user_id)
    _ai()._ctx.pop(group_key, None)
    return "本会话 AI 上下文已清空。"


async def _t_now(bot, event, args) -> str:
    now = datetime.now().astimezone()
    week = "一二三四五六日"[now.weekday()]
    return _j({
        "日期": now.strftime("%Y-%m-%d"),
        "时间": now.strftime("%H:%M:%S"),
        "星期": f"星期{week}",
        "时区": now.tzname() or str(now.utcoffset()),
        "ISO": now.isoformat(timespec="seconds"),
        "Unix时间戳": int(now.timestamp()),
    })


def _safe_calc(expr: str) -> Any:
    """白名单 AST 求值：仅数字与四则运算/幂/取余，杜绝任意代码执行。"""
    import ast as _ast
    import operator as _op

    bin_ops = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
               _ast.Div: _op.truediv, _ast.FloorDiv: _op.floordiv,
               _ast.Mod: _op.mod, _ast.Pow: _op.pow}
    unary_ops = {_ast.UAdd: _op.pos, _ast.USub: _op.neg}

    def ev(node):
        if isinstance(node, _ast.Expression):
            return ev(node.body)
        if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, _ast.BinOp) and type(node.op) in bin_ops:
            return bin_ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in unary_ops:
            return unary_ops[type(node.op)](ev(node.operand))
        raise ValueError("仅支持数字与 + - * / // % ** 运算")

    return ev(_ast.parse(expr, mode="eval"))


async def _t_send_voice(bot, event, args) -> str:
    from src.plugins.voice import send_voice

    return await send_voice(bot, event, str(args.get("text", "")))


async def _t_voice_reply_toggle(bot, event, args) -> str:
    from src.plugins.voice import set_voice_reply

    enabled = bool(args.get("enabled"))
    await set_voice_reply(event, enabled)
    return "已开启：之后我在当前会话默认用语音回复" if enabled else "已关闭：之后改回文字回复"


async def _t_send_selfie(bot, event, args) -> str:
    from src.plugins.selfie import send_selfie

    return await send_selfie(bot, event)


async def _t_group_honor(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await groupextras.honor_text(bot, int(gid))


async def _t_member_card(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not gid or not isinstance(uid, int):
        return "错误：需要 user_id（整数）；群聊中 group_id 默认当前群。"
    ok, msg = await groupextras.set_card(bot, int(gid), uid, str(args.get("card", "")))
    return msg if ok else f"执行失败：{msg}"


async def _t_member_title(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not gid or not isinstance(uid, int):
        return "错误：需要 user_id（整数）；群聊中 group_id 默认当前群。"
    ok, msg = await groupextras.set_title(bot, int(gid), uid, str(args.get("title", "")))
    return msg if ok else f"执行失败：{msg}"


async def _t_send_like(bot, event, args) -> str:
    from src.plugins import interact
    ok, msg = await interact.give_like(bot, event.user_id)
    return msg if ok else f"没点成：{msg}"


async def _t_poke(bot, event, args) -> str:
    from src.plugins import interact
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id") or event.user_id
    if not gid:
        return "错误：戳一戳只能在群里用（需要 group_id）。"
    ok, msg = await interact.poke(bot, int(gid), int(uid))
    return msg if ok else f"没戳成：{msg}"


async def _t_group_files(bot, event, args) -> str:
    from src.plugins import interact
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await interact.files_text(bot, int(gid), str(args.get("keyword", "")))


async def _t_set_signature(bot, event, args) -> str:
    from src.plugins import interact
    ok, msg = await interact.set_signature(bot, str(args.get("text", "")))
    return msg if ok else f"执行失败：{msg}"


async def _t_voice_character(bot, event, args) -> str:
    from src.plugins import voice
    gid = getattr(event, "group_id", None) or voice._api_group(event)
    return await voice.choose_character(bot, gid, str(args.get("character", "")))


async def _t_web_search(bot, event, args) -> str:
    from src.plugins import webtools
    return await webtools.search_text(str(args.get("query", "")))


async def _t_weather(bot, event, args) -> str:
    from src.plugins import webtools
    return await webtools.weather_text(str(args.get("city", "")))


async def _t_rate(bot, event, args) -> str:
    from src.plugins import webtools
    try:
        amount = float(args.get("amount", 1) or 1)
    except (TypeError, ValueError):
        amount = 1.0
    return await webtools.rate_text(str(args.get("from", "")), str(args.get("to", "")), amount)


async def _t_reminder_create(bot, event, args) -> str:
    from datetime import datetime, timedelta

    from src.plugins import reminders
    gid = getattr(event, "group_id", None)
    due = None
    if args.get("after_minutes") is not None:
        try:
            due = datetime.now() + timedelta(minutes=float(args["after_minutes"]))
        except (TypeError, ValueError):
            pass
    elif args.get("when"):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                due = datetime.strptime(str(args["when"]).strip(), fmt)
                break
            except ValueError:
                continue
    if due is None:
        return "错误：需要 after_minutes（多少分钟后）或 when（格式 YYYY-MM-DD HH:MM，本地时间；不确定当前时间先调用 get_current_time）。"
    ok, msg = await reminders.create(event.user_id, gid, due, str(args.get("content", "")))
    return msg if ok else f"没设成功：{msg}"


async def _t_reminder_list(bot, event, args) -> str:
    from src.plugins import reminders
    return await reminders.list_text(event.user_id)


async def _t_reminder_cancel(bot, event, args) -> str:
    from src.plugins import reminders
    ok, msg = await reminders.cancel(event.user_id, int(args.get("id", 0) or 0))
    return msg


async def _t_join_verify(bot, event, args) -> str:
    from src.plugins import joinverify
    return await joinverify.set_config(
        enabled=args.get("enabled"), action=args.get("action"), minutes=args.get("minutes"))


async def _t_poll_create(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：投票只能在群里发起。"
    opts = args.get("options") or []
    ok, msg = polls.create_poll(int(gid), event.user_id, str(args.get("question", "")),
                                [str(o) for o in opts], int(args.get("minutes") or polls.DEFAULT_MINUTES))
    if ok:
        try:
            await bot.call_api("send_group_msg", group_id=int(gid), message=msg)
        except Exception as e:
            return f"投票已创建但发送到群失败：{e}"
        return "投票已发到群里，截止后会自动公布结果。"
    return f"没发起成功：{msg}"


async def _t_poll_result(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    return polls.get_result(int(gid)) if gid else "错误：需要在群里查询。"


async def _t_poll_end(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要在群里操作。"
    ok, msg = polls.end_poll(int(gid), event.user_id)
    if ok:
        try:
            await bot.call_api("send_group_msg", group_id=int(gid), message=msg)
        except Exception:
            pass
        return "投票已结束，结果已发到群里。"
    return msg


async def _t_generate_image(bot, event, args) -> str:
    from src.plugins import imagegen
    seg, tip = await imagegen.generate(event.user_id, str(args.get("prompt", "")))
    if seg is None:
        return f"没画成：{tip}"
    try:
        await bot.send(event, seg)
    except Exception as e:
        return f"图片已生成但发送失败：{e}"
    return "图片已发送，请用一句话简短自然地回应（不要描述图片细节）。"


async def _t_invites_list(bot, event, args) -> str:
    from src.plugins import invites
    return invites.list_text()


async def _t_invite_resolve(bot, event, args) -> str:
    from src.plugins import invites
    seq, approve = args.get("seq"), args.get("approve")
    if not isinstance(seq, int) or not isinstance(approve, bool):
        return "错误：需要 seq（整数，可先用 list_pending_invites 查看）与 approve（布尔值）。"
    ok, msg = await invites.resolve(bot, seq, approve, str(args.get("reason", ""))[:50])
    return msg if ok else f"错误：{msg}"


async def _t_essence_set(bot, event, args) -> str:
    from src.plugins import essence
    mid = args.get("message_id")
    if not mid and getattr(event, "reply", None) is not None:
        mid = event.reply.message_id
    if not mid:
        return "错误：需要 message_id，或引用要设为精华的消息后再让我设置。"
    ok, msg = await essence.set_essence(bot, int(mid))
    return msg if ok else f"执行失败：{msg}"


async def _t_essence_delete(bot, event, args) -> str:
    from src.plugins import essence
    mid = args.get("message_id")
    if not mid and getattr(event, "reply", None) is not None:
        mid = event.reply.message_id
    if not mid:
        return "错误：需要 message_id，或引用精华消息后再让我取消。"
    ok, msg = await essence.delete_essence(bot, int(mid))
    return msg if ok else f"执行失败：{msg}"


async def _t_essence_list(bot, event, args) -> str:
    from src.plugins import essence
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await essence.list_essence(bot, int(gid))


async def _t_send_sticker(bot, event, args) -> str:
    from src.plugins import stickers

    count = await stickers.ensure_library(bot)
    if not count:
        return "表情包库是空的（机器人账号里还没有收藏表情，或还没来得及打标签）。"
    sid = await stickers.pick_by_mood(str(args.get("mood", "")))
    msg = stickers.sticker_message(sid) if sid else None
    if msg is None:
        return "没找到合适的表情包。"
    await bot.send(event, msg)
    return "已发送一张表情包；文字回复保持简短自然，不要描述这张图。"


async def _t_calc(bot, event, args) -> str:
    expr = str(args.get("expression", ""))[:200]
    if not expr:
        return "错误：缺少 expression。"
    try:
        result = _safe_calc(expr)
        r = int(result) if isinstance(result, float) and result.is_integer() else result
        return _j({"表达式": expr, "结果": r})
    except ZeroDivisionError:
        return "错误：除数为零。"
    except Exception as e:
        return f"错误：{e}"


async def _t_sensitive_list(bot, event, args) -> str:
    from src.plugins import sensitive as sp

    cfg = await sp.sensitive_config()
    words = await sp.get_words()
    return _j({
        "监控开关": "开启" if cfg["sensitive_enabled"] else "关闭",
        "词数": len(words),
        "词库": words,
    })


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


def build_tools() -> list[tuple[dict, Any]]:
    """返回全部工具注册表（含仅超管的）。

    权限过滤不在这里做：能力清单需要展示全部能力（标注【仅超管】），
    真正的调用权限在 chat() 中按 permitted 过滤，双重保证不越权。
    """
    tools = [
        _tool("get_group_info", "获取群聊信息（群名、成员数）",
              {"type": "object", "properties": {"group_id": {"type": "integer", "description": "群号，默认当前群"}}, "required": []},
              "all", _t_group_info),
        _tool("get_member_list", "获取群成员列表（QQ、昵称、群名片、角色）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_member_list),
        _tool("get_member_info", "获取指定成员的详细信息",
              {"type": "object", "properties": {"user_id": {"type": "integer", "description": "成员 QQ"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "all", _t_member_info),
        _tool("get_active_stats", "查询群活跃统计（消息量、参与人数、发言Top5）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}, "day": {"type": "string", "description": "YYYY-MM-DD，默认今天"}}, "required": []},
              "all", _t_stats),
        _tool("mute_member", "禁言群成员（分钟）",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "duration_minutes": {"type": "integer", "description": "默认10，上限43200"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_mute),
        _tool("unmute_member", "解除成员禁言",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_unmute),
        _tool("kick_member", "将成员移出群聊",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_kick),
        _tool("set_whole_ban", "开启/关闭全体禁言",
              {"type": "object", "properties": {"enable": {"type": "boolean"}, "group_id": {"type": "integer"}}, "required": ["enable"]},
              "superuser", _t_whole_ban),
        _tool("list_whitelist", "查看群白名单",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_whitelist_list),
        _tool("add_whitelist_group", "添加群到白名单",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
              "superuser", _t_whitelist_add),
        _tool("remove_whitelist_group", "从白名单移除群（env 来源除外）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
              "superuser", _t_whitelist_del),
        _tool("set_group_business", "开启/关闭某群的业务（临时开关）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["group_id", "enabled"]},
              "superuser", _t_group_switch),
        _tool("get_github_trending", "获取 GitHub 当前趋势项目榜单（名称、描述、star 数）",
              {"type": "object", "properties": {
                  "since": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "默认 daily"},
                  "language": {"type": "string", "description": "编程语言筛选，如 python，留空为全部"},
              }, "required": []},
              "all", _t_gh_trending),
        _tool("get_current_time", "获取当前的日期、时间、星期与时区（回答任何与当前时间/日期/星期相关的问题前必须先调用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_now),
        _tool("send_voice", "在当前会话发送一条语音消息，把 text 用语音念出来（用户说“发语音/用语音说/念给我听/语音回复我”时使用；text 是要说的话，口语化、300 字内；发送成功后文字回复尽量简短，不要重复语音内容）",
              {"type": "object", "properties": {"text": {"type": "string", "description": "要用语音说的内容"}}, "required": ["text"]},
              "all", _t_send_voice),
        _tool("set_voice_reply", "开启/关闭当前会话的默认语音回复（开启后我的回复默认以语音发出；用户说“以后都用语音回复/别发语音了、用文字”时使用）",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_voice_reply_toggle),
        _tool("send_selfie", "发送一张星潮本人的照片/自拍到当前会话（用户说“看看你的照片/发张自拍/你长什么样/给我看看你”时使用；发送后文字回复简短自然，不要描述图片细节）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_send_selfie),
        _tool("calculate", "进行精确的算术计算（加减乘除、取余、幂运算）",
              {"type": "object", "properties": {"expression": {"type": "string", "description": "算术表达式，如 (3+4)*2 或 2**10"}}, "required": ["expression"]},
              "all", _t_calc),
        _tool("get_pending_join_requests", "查看待审批的入群申请列表（序号、QQ、回答）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_pending_requests),
        _tool("approve_join_request", "通过入群申请",
              {"type": "object", "properties": {"seq": {"type": "integer", "description": "申请序号"}}, "required": ["seq"]},
              "superuser", _t_approve_request),
        _tool("reject_join_request", "拒绝入群申请",
              {"type": "object", "properties": {"seq": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["seq"]},
              "superuser", _t_reject_request),
        _tool("list_appeals", "查看待处理的违规申诉（编号、申诉人、被处罚原文、申诉理由）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_appeal_list),
        _tool("resolve_appeal", "通过或驳回违规申诉（通过会自动解除禁言并私聊通知申诉人；先用 list_appeals 获取编号）",
              {"type": "object", "properties": {"appeal_id": {"type": "integer", "description": "申诉编号"}, "approve": {"type": "boolean", "description": "true=通过，false=驳回"}, "note": {"type": "string", "description": "给申诉人的备注/驳回理由（可空）"}}, "required": ["appeal_id", "approve"]},
              "superuser", _t_appeal_resolve),
        _tool("list_replies", "查看关键词词库",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_reply_list),
        _tool("reload_replies", "重载关键词词库",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_reply_reload),
        _tool("set_reply_enabled", "开启/关闭关键词回复模块",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_reply_toggle),
        _tool("set_link_preview_enabled", "开启/关闭链接自动解读功能（群里发链接时 AI 一句话概括）",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_link_preview_toggle),
        _tool("set_vision_guard_enabled", "开启/关闭图片 AI 识别与违规图片自动撤回禁言",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_vision_guard_toggle),
        _tool("set_vision_model", "设置图片识别用的视觉模型（需支持图片输入）",
              {"type": "object", "properties": {"model": {"type": "string", "description": "如 glm-4v-flash"}}, "required": ["model"]},
              "superuser", _t_vision_model_set),
        _tool("get_group_honor", "查看本群（或指定群）的龙王、群聊之火等荣誉成员（用户问“谁是龙王/群里谁最活跃有荣誉”时使用）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_group_honor),
        _tool("set_member_card", "修改某成员的群名片（机器人需是群管理员；card 留空则清除）",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "card": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_member_card),
        _tool("set_member_title", "给某成员设置专属头衔（最多 6 个字，机器人需是群主；title 留空则清除）",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "title": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_member_title),
        _tool("send_like", "给提问的人点赞（QQ 资料卡点赞，每人每天一次；用户说“给我点个赞/赞我”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_send_like),
        _tool("poke_member", "戳一戳群成员（用户说“戳戳他/戳我一下”时使用；user_id 不填则戳提问的人；同群 20 秒冷却）",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": []},
              "all", _t_poke),
        _tool("list_group_files", "查看群文件（可按文件名关键词搜索，结果少时附下载链接；用户问“群里有没有××文件/群文件有什么”时使用）",
              {"type": "object", "properties": {"keyword": {"type": "string", "description": "文件名关键词，留空列出最近文件"}, "group_id": {"type": "integer"}}, "required": []},
              "all", _t_group_files),
        _tool("set_bot_signature", "修改机器人账号的个性签名（最多 60 字，留空清空）",
              {"type": "object", "properties": {"text": {"type": "string"}}, "required": []},
              "superuser", _t_set_signature),
        _tool("set_voice_character", "切换/查看 QQ AI 语音角色（character 留空列出可选角色；可填序号或名称）",
              {"type": "object", "properties": {"character": {"type": "string"}}, "required": []},
              "superuser", _t_voice_character),
        _tool("web_search", "联网搜索网页（需要最新信息、新闻、版本号、你不确定的事实时使用；返回标题/摘要/链接，回答时说明信息来自网络搜索）",
              {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
              "all", _t_web_search),
        _tool("get_weather", "查询城市天气（当前 + 未来三天；用户问“北京天气怎么样/明天下雨吗”时使用）",
              {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
              "all", _t_weather),
        _tool("get_exchange_rate", "查询汇率/换算金额（币种用三位代码，如 USD CNY JPY EUR；用户问“100美元多少人民币”时使用）",
              {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "amount": {"type": "number"}}, "required": ["from", "to"]},
              "all", _t_rate),
        _tool("create_reminder", "给提问的人设置个人提醒（到点在原来的会话里 @ 他；用户说“明天8点提醒我开会/半小时后叫我”时使用；after_minutes 与 when 二选一，when 为本地时间 YYYY-MM-DD HH:MM，不确定当前时间先调用 get_current_time）",
              {"type": "object", "properties": {"content": {"type": "string"}, "after_minutes": {"type": "number"}, "when": {"type": "string"}}, "required": ["content"]},
              "all", _t_reminder_create),
        _tool("list_my_reminders", "查看提问的人自己的待触发提醒",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_reminder_list),
        _tool("cancel_reminder", "取消提问的人自己的某条提醒（先用 list_my_reminders 查编号）",
              {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
              "all", _t_reminder_cancel),
        _tool("set_join_verify", "设置新人入群验证（新人入群后须回答算术题；超时或答错 3 次时先在群里 @ 公示并宽限 30 秒，再提醒超管或踢出）：enabled 开关，action 为 notify（仅提醒超管）或 kick（踢出，需机器人是管理员），minutes 时限 1–60；不传参数则只查看当前设置",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}, "action": {"type": "string", "enum": ["notify", "kick"]}, "minutes": {"type": "integer"}}, "required": []},
              "superuser", _t_join_verify),
        _tool("create_poll", "在群里发起投票（2–8 个选项，默认 30 分钟，到时自动公布结果；用户说“发起个投票：晚上吃啥 火锅 烧烤”时使用）",
              {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "minutes": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["question", "options"]},
              "all", _t_poll_create),
        _tool("get_poll_result", "查看本群当前投票的票数",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_poll_result),
        _tool("end_poll", "提前结束本群投票并公布结果（仅发起人或超管有效）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_poll_end),
        _tool("generate_image", "AI 画图并发送到当前会话（用户说“画一张/帮我画”时使用；每人每天有限额；发送后文字回应简短自然）",
              {"type": "object", "properties": {"prompt": {"type": "string", "description": "画面描述"}}, "required": ["prompt"]},
              "all", _t_generate_image),
        _tool("list_pending_invites", "查看待处理的好友申请和拉群邀请（有人加机器人好友、或把机器人拉进新群时会进入这里）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_invites_list),
        _tool("resolve_invite", "通过或拒绝好友申请/拉群邀请（先用 list_pending_invites 获取序号）",
              {"type": "object", "properties": {"seq": {"type": "integer", "description": "序号"}, "approve": {"type": "boolean"}, "reason": {"type": "string", "description": "拒绝理由（可空）"}}, "required": ["seq", "approve"]},
              "superuser", _t_invite_resolve),
        _tool("set_essence_message", "把一条群消息设为精华（用户引用那条消息说“设为精华”，或提供 message_id；机器人需要是群管理员）",
              {"type": "object", "properties": {"message_id": {"type": "integer", "description": "消息 ID，缺省取被引用的消息"}}, "required": []},
              "superuser", _t_essence_set),
        _tool("delete_essence_message", "取消一条群精华消息（引用该消息或提供 message_id）",
              {"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": []},
              "superuser", _t_essence_delete),
        _tool("list_essence_messages", "查看本群（或指定群）的精华消息列表",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_essence_list),
        _tool("send_sticker", "发一张表情包到当前会话（从机器人收藏的表情包库里按心情挑；聊天气氛合适、想用表情包回应时使用，如 无语/开心/狗头/委屈/吃瓜/加油）",
              {"type": "object", "properties": {"mood": {"type": "string", "description": "想表达的心情或梗，如 无语、开心、狗头"}}, "required": ["mood"]},
              "all", _t_send_sticker),
        _tool("run_capability_check", "探测当前 QQ 连接（NapCat）支持哪些能力（精华、收藏表情包、OCR、历史消息等，只读不改动；用户问“你现在有哪些 QQ 能力/支持精华吗”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_capability_check),
        _tool("get_my_memory", "查看你（机器人）对当前提问者记住了什么（长期记忆里关于 TA 的印象与熟悉度；用户问“你记得我什么/你了解我吗”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_memory_get),
        _tool("forget_my_memory", "清除对当前提问者的全部印象并停止记录 TA 的发言（用户说“忘了我/别记我/清除我的记忆”时使用，执行前简短确认）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_memory_forget),
        _tool("resume_my_memory", "恢复记录当前提问者（用户说“可以记住我了/重新记我”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_memory_resume),
        _tool("set_proactive_mode", "设置机器人在群里的主动程度档位：安静（很少插话）/ 正常 / 话痨（更爱接话、评论图片、复读）；默认设置当前群，group_id 填“全局”则改全局默认（用户说“话痨一点/安静点/少说话”时使用）",
              {"type": "object", "properties": {"mode": {"type": "string", "enum": ["安静", "正常", "话痨"]}, "group_id": {"type": "string", "description": "群号或“全局”，默认当前群"}}, "required": ["mode"]},
              "superuser", _t_proactive_mode),
        _tool("set_proactive_enabled", "开启/关闭群聊主动性（话题明确或有人求助时 AI 自动参与讨论）",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_proactive_toggle),
        _tool("list_sensitive_words", "查看敏感词词库（命中即撤回，可选禁言）与监控开关状态",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_sensitive_list),
        _tool("add_sensitive_words", "批量添加敏感词（全局词库，命中后自动撤回/禁言；用户说“把 xxx 加进敏感词/违禁词”时使用）",
              {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}, "description": "要添加的敏感词列表，可一次多个"}}, "required": ["words"]},
              "superuser", _t_sensitive_add),
        _tool("remove_sensitive_words", "批量移除敏感词（全局词库）",
              {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}, "description": "要移除的敏感词列表，可一次多个"}}, "required": ["words"]},
              "superuser", _t_sensitive_remove),
        _tool("recall_message", "撤回一条消息（回复目标消息后提出，或提供 message_id）",
              {"type": "object", "properties": {"message_id": {"type": "integer", "description": "可选；不填则撤回当前引用的消息"}}, "required": []},
              "superuser", _t_recall),
        _tool("ping", "连通测试",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_ping),
        _tool("get_my_id", "查看当前群号 / 用户 QQ / 机器人 ID",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_my_id),
        _tool("get_help", "查看完整指令菜单（仅当用户明确索要完整功能/指令列表、完整帮助时才调用；用户只是问“你能做什么”时用你自己的话自然介绍，不要调用本工具）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_help),
        _tool("get_about", "获取机器人与开发者信息",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_about),
        _tool("search_blog", "搜索开发者博客（xuyi.dev）的文章与作品集。当用户求推荐软件/工具、求助技术问题、聊到编程学习（Git、Rust、Unity、AI 开发等）或可能与博主作品相关的话题时，先调用本工具查询；找到相关内容后优先推荐",
              {"type": "object", "properties": {"keywords": {"type": "string", "description": "主题关键词，如 Mac 剪贴板、git 分支、端口占用"}}, "required": ["keywords"]},
              "all", _t_blog_search),
        _tool("get_blog_post", "读取某篇博客文章的摘要与正文节选（先用 search_blog 找到文章，再按标题或链接读取）",
              {"type": "object", "properties": {"url_or_title": {"type": "string", "description": "文章链接或标题关键词"}}, "required": ["url_or_title"]},
              "all", _t_blog_post),
        _tool("refresh_blog", "刷新博客知识库（重新抓取 xuyi.dev 的最新文章列表）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_blog_refresh),
        _tool("set_blog_enabled", "开启/关闭博客知识关联推荐（关闭后 AI 不再优先推荐博客内容）",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_blog_toggle),
        _tool("get_welcome", "查看进群欢迎语及开关状态",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_welcome_view),
        _tool("set_welcome", "设置进群欢迎语（支持占位符 {at}=@新人、{qq}=新人QQ、{group}=群号）",
              {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
              "superuser", _t_welcome_set),
        _tool("set_welcome_enabled", "开启/关闭新人进群欢迎",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_welcome_toggle),
        _tool("publish_group_notice", "发布群公告",
              {"type": "object", "properties": {"content": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["content"]},
              "superuser", _t_notice_publish),
        _tool("get_group_notices", "查看当前群公告列表",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "superuser", _t_notice_list),
        _tool("send_group_message", "让机器人在指定群发送一条普通群消息（仅限白名单群；与「群公告」不同；用户说“在群里说一句/帮我发条消息”时使用）",
              {"type": "object", "properties": {"group_id": {"type": "integer", "description": "目标群号，群聊中默认当前群；私聊时必须指定"}, "text": {"type": "string", "description": "要发送的文本内容"}}, "required": ["text"]},
              "superuser", _t_group_send),
        _tool("get_changelog", "查看机器人最近一次更新了什么（用户问“最近更新了啥/有什么新功能”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_changelog),
        _tool("run_backup", "立即备份数据库并列出现有备份（用户说“备份一下数据”时使用）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_run_backup),
        _tool("list_feed_subscriptions", "查看内容推送订阅（早报、GitHub 趋势等定时推送到群的订阅）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_feed_list),
        _tool("run_feed_now", "立即执行一次内容推送订阅（不影响当天定时推送）；先用 list_feed_subscriptions 查 id",
              {"type": "object", "properties": {"id": {"type": "integer", "description": "订阅 id"}}, "required": ["id"]},
              "superuser", _t_feed_now),
        _tool("list_scheduled_tasks", "查看定时任务列表（可用对话创建/删除/启停）",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_task_list),
        _tool("create_scheduled_task", "创建定时任务（到点自动在群里发消息；用户说“每天9点提醒/每周一提醒”时使用；仅限白名单群）",
              {"type": "object", "properties": {
                  "time": {"type": "string", "description": "触发时间 HH:MM（北京时间）"},
                  "message": {"type": "string", "description": "到点发送的内容"},
                  "repeat": {"type": "string", "enum": ["daily", "weekdays", "weekend", "weekly", "once"], "description": "默认 daily"},
                  "weekday": {"type": "integer", "description": "repeat=weekly 时必填：0=周一 … 6=周日"},
                  "date": {"type": "string", "description": "repeat=once 时必填：YYYY-MM-DD"},
                  "at_all": {"type": "boolean", "description": "是否 @全体成员，默认否（需要机器人为群管理员）"},
                  "group_id": {"type": "integer", "description": "目标群号，群聊中默认当前群"},
              }, "required": ["time", "message"]},
              "superuser", _t_task_create),
        _tool("delete_scheduled_task", "删除定时任务（需要 task_id，可用 list_scheduled_tasks 查看编号）",
              {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
              "superuser", _t_task_delete),
        _tool("toggle_scheduled_task", "启用/停用定时任务（需要 task_id 与 enabled）",
              {"type": "object", "properties": {"task_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["task_id", "enabled"]},
              "superuser", _t_task_toggle),
        _tool("list_superusers", "查看超级管理员列表",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_superuser_list),
        _tool("add_superuser", "添加超级管理员",
              {"type": "object", "properties": {"user_id": {"type": "integer", "description": "QQ 号"}}, "required": ["user_id"]},
              "superuser", _t_superuser_add),
        _tool("remove_superuser", "移除超级管理员（env 来源除外）",
              {"type": "object", "properties": {"user_id": {"type": "integer", "description": "QQ 号"}}, "required": ["user_id"]},
              "superuser", _t_superuser_del),
        _tool("set_ai_enabled", "开启/关闭 AI 问答功能",
              {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
              "superuser", _t_ai_toggle),
        _tool("get_ai_status", "查看 AI 问答状态、模型、限额与今日用量",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_ai_status),
        _tool("clear_ai_context", "清空本群/本会话的 AI 对话上下文",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_ai_clear),
    ]
    return tools


