"""定时任务：管理员配置定时向群内发送消息（支持 @全体、重复规则）。

- 存储：SQLite scheduled_tasks 表
- 规则：daily（每天）/ weekdays（工作日）/ weekend（周末）/ weekly（每周指定星期）/ once（指定日期一次）
- 调度：后台协程每 30 秒扫描，按北京时间（TZ=Asia/Shanghai）触发，同一次触发有去重
- 指令：/task list（超管）；增删改在 Web 管理面板「定时任务」页
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from nonebot import get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg
from nonebot.log import logger

from src.permission import SUPERUSER
from src.store import get_store

_fired: set[tuple[int, str]] = set()  # (task_id, "YYYY-MM-DD HH:MM") 去重
_worker_started = False
REPEAT_LABEL = {
    "daily": "每天", "weekdays": "工作日", "weekend": "周末",
    "weekly": "每周指定日", "once": "仅一次",
}
WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _matches_today(task: dict, now) -> bool:
    repeat = task["repeat"]
    wd = now.weekday()
    if repeat == "daily":
        return True
    if repeat == "weekdays":
        return wd < 5
    if repeat == "weekend":
        return wd >= 5
    if repeat == "weekly":
        return task.get("weekday") is not None and int(task["weekday"]) == wd
    if repeat == "once":
        return task.get("date") == now.strftime("%Y-%m-%d")
    return False


async def _fire(bot, task: dict) -> None:
    message = MessageSegment.at("all") + " " + task["message"] if task["at_all"] \
        else MessageSegment.text(task["message"])
    try:
        await bot.call_api("send_group_msg", group_id=task["group_id"], message=message)
        logger.info(f"定时任务 #{task['id']} 已触发：群 {task['group_id']}")
    except Exception:
        logger.exception(f"定时任务 #{task['id']} 发送失败：群 {task['group_id']}")


async def _worker() -> None:
    logger.info("定时任务调度器已启动（每 30 秒扫描）")
    while True:
        try:
            now = datetime.now()
            key_now = now.strftime("%Y-%m-%d %H:%M")
            tasks = await get_store().list_tasks(enabled_only=True)
            for task in tasks:
                key = (task["id"], key_now)
                if task["time"] == now.strftime("%H:%M") and key not in _fired \
                        and _matches_today(task, now):
                    _fired.add(key)
                    # 清理 48 小时前的去重记录
                    for k in [k for k in _fired if k[1] < key_now]:
                        _fired.discard(k)
                    await _fire(_get_bot(), task)
        except Exception:
            logger.exception("定时任务扫描异常")
        await asyncio.sleep(30)


def _get_bot():
    from nonebot import get_bot

    return get_bot()


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())


task_cmd = on_command("task", rule=SUPERUSER, priority=1, block=True)


@task_cmd.handle()
async def handle_task(matcher, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    if raw in ("list", "列表", ""):
        tasks = await get_store().list_tasks()
        if not tasks:
            await matcher.send("当前没有定时任务（面板「定时任务」页可添加）。")
            return
        lines = ["定时任务："]
        for t in tasks:
            desc = REPEAT_LABEL.get(t["repeat"], t["repeat"])
            if t["repeat"] == "weekly" and t["weekday"] is not None:
                desc += f"（{WEEKDAY_NAME[t['weekday']]}）"
            if t["repeat"] == "once" and t["date"]:
                desc += f"（{t['date']}）"
            lines.append(
                f"  #{t['id']} [{t['time']}] {desc} → 群 {t['group_id']}"
                f"{'（@全体）' if t['at_all'] else ''} {'✅' if t['enabled'] else '⏸'}"
            )
        await matcher.send("\n".join(lines))
        return
    await matcher.send("定时任务的增删改请在 Web 管理面板「定时任务」页操作；/task list 查看列表。")
