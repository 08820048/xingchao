"""AI 工具：定时任务、订阅推送与个人提醒。"""

from __future__ import annotations

import re
from datetime import datetime

from src.aitools.registry import _j, tool
from src.store import get_store


@tool(
    "list_scheduled_tasks",
    "查看定时任务列表（可用对话创建/删除/启停）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
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


@tool(
    "create_scheduled_task",
    "创建定时任务（到点自动在群里发消息；用户说“每天9点提醒/每周一提醒”时使用；仅限白名单群）",
    {"type": "object", "properties": {
        "time": {"type": "string", "description": "触发时间 HH:MM（北京时间）"},
        "message": {"type": "string", "description": "到点发送的内容"},
        "repeat": {"type": "string", "enum": ["daily", "weekdays", "weekend", "weekly", "once"], "description": "默认 daily"},
        "weekday": {"type": "integer", "description": "repeat=weekly 时必填：0=周一 … 6=周日"},
        "date": {"type": "string", "description": "repeat=once 时必填：YYYY-MM-DD"},
        "at_all": {"type": "boolean", "description": "是否 @全体成员，默认否（需要机器人为群管理员）"},
        "group_id": {"type": "integer", "description": "目标群号，群聊中默认当前群"},
    }, "required": ["time", "message"]},
    "superuser",
)
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


@tool(
    "delete_scheduled_task",
    "删除定时任务（需要 task_id，可用 list_scheduled_tasks 查看编号）",
    {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
    "superuser",
)
async def _t_task_delete(bot, event, args) -> str:
    tid = args.get("task_id")
    if not isinstance(tid, int):
        return "错误：需要 task_id（可用 list_scheduled_tasks 查看编号）。"
    ok = await get_store().delete_task(int(tid))
    return f"定时任务 #{tid} 已删除。" if ok else f"错误：任务 #{tid} 不存在。"


@tool(
    "toggle_scheduled_task",
    "启用/停用定时任务（需要 task_id 与 enabled）",
    {"type": "object", "properties": {"task_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["task_id", "enabled"]},
    "superuser",
)
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


@tool(
    "list_feed_subscriptions",
    "查看内容推送订阅（早报、GitHub 趋势等定时推送到群的订阅）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
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


@tool(
    "run_feed_now",
    "立即执行一次内容推送订阅（不影响当天定时推送）；先用 list_feed_subscriptions 查 id",
    {"type": "object", "properties": {"id": {"type": "integer", "description": "订阅 id"}}, "required": ["id"]},
    "superuser",
)
async def _t_feed_now(bot, event, args) -> str:
    from src.plugins.feeds import SOURCES, run_sub
    sid = int(args["id"])
    sub = next((s for s in await get_store().list_feed_subs() if s["id"] == sid), None)
    if sub is None or sub["source"] not in SOURCES:
        return _j({"ok": False, "错误": "订阅不存在"})
    ok, msg = await run_sub(bot, sub)
    return _j({"ok": ok, "结果": msg})


@tool(
    "create_reminder",
    "给提问的人设置个人提醒（到点在原来的会话里 @ 他；用户说“明天8点提醒我开会/半小时后叫我”时使用；after_minutes 与 when 二选一，when 为本地时间 YYYY-MM-DD HH:MM，不确定当前时间先调用 get_current_time）",
    {"type": "object", "properties": {"content": {"type": "string"}, "after_minutes": {"type": "number"}, "when": {"type": "string"}}, "required": ["content"]},
    "all",
)
async def _t_reminder_create(bot, event, args) -> str:
    from datetime import timedelta

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


@tool(
    "list_my_reminders",
    "查看提问的人自己的待触发提醒",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_reminder_list(bot, event, args) -> str:
    from src.plugins import reminders
    return await reminders.list_text(event.user_id)


@tool(
    "cancel_reminder",
    "取消提问的人自己的某条提醒（先用 list_my_reminders 查编号）",
    {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    "all",
)
async def _t_reminder_cancel(bot, event, args) -> str:
    from src.plugins import reminders
    ok, msg = await reminders.cancel(event.user_id, int(args.get("id", 0) or 0))
    return msg
