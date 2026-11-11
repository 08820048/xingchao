"""个人提醒：到点在原来的群里 @ 你（私聊发起的在私聊里提醒）。

- /提醒 <时间> <内容>：时间可写 "10分钟后"、"2小时后"、"明天8点"、"今晚8点半"、"20:30"、"后天 9:00"、
  "2026-12-01 08:00"；/提醒列表（别名 /我的提醒）；/取消提醒 <编号>
- AI 工具：create_reminder / list_my_reminders / cancel_reminder（用户说"明天早上8点提醒我开会"时使用）
- 存 SQLite reminders 表，重启不丢；每 20 秒扫描一次；机器人离线期间错过的提醒，恢复后补发并标注"迟到"
- 限制：每人最多 10 条未触发的提醒；最早 1 分钟后、最晚 1 年内；内容最多 100 字
- 与 scheduler.py 的区别：scheduler 是管理员配置的群定时消息（可重复、可 @全体）；这里是成员个人的一次性提醒
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta

from nonebot import get_bots, get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import BASIC
from src.store import get_store

MAX_PER_USER = 10
MAX_CONTENT = 100
MIN_AHEAD_SECONDS = 60
MAX_AHEAD_DAYS = 365
LATE_SECONDS = 300
SCAN_SECONDS = 20

_worker_started = False

_REL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(分钟|分|小时|个小时|钟头|h|min|天)\s*(?:以?后|之后)\s*")
_DAY = re.compile(r"^\s*(今天|今晚|今早|明天|明早|明晚|后天)?\s*")
_CLOCK = re.compile(r"^\s*(\d{1,2})\s*(?:[:：]|点|时)\s*(半|\d{1,2})?\s*分?\s*")
_DATE = re.compile(r"^\s*(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})日?\s*")


def parse_when(text: str, now: datetime) -> tuple[datetime, str] | None:
    """从文字开头解析时间，返回 (时间点, 剩余内容)；解析不了返回 None。"""
    m = _REL.match(text)
    if m:
        n = float(m.group(1))
        unit = m.group(2)
        delta = (
            timedelta(minutes=n) if unit in ("分钟", "分", "min")
            else timedelta(hours=n) if unit in ("小时", "个小时", "钟头", "h")
            else timedelta(days=n)
        )
        return now + delta, text[m.end():].strip()

    rest = text
    date = None
    dm = _DATE.match(rest)
    if dm:
        try:
            date = datetime(int(dm.group(1) or now.year), int(dm.group(2)), int(dm.group(3))).date()
        except ValueError:
            return None
        rest = rest[dm.end():]
    day_word = None
    if date is None:
        wm = _DAY.match(rest)
        day_word = wm.group(1) if wm else None
        rest = rest[wm.end():] if wm else rest
    cm = _CLOCK.match(rest)
    if not cm:
        return None
    hour = int(cm.group(1))
    minute = 30 if cm.group(2) == "半" else int(cm.group(2) or 0)
    if day_word in ("今晚", "明晚") and hour < 12:
        hour += 12
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    if date is None:
        base = now.date()
        if day_word in ("明天", "明早", "明晚"):
            base += timedelta(days=1)
        elif day_word == "后天":
            base += timedelta(days=2)
        date = base
    due = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
    if day_word is None and dm is None and due <= now:
        due += timedelta(days=1)  # 只写了钟点且已过：顺延到明天
    return due, rest[cm.end():].strip()


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


async def create(user_id: int, group_id: int | None, due: datetime, content: str) -> tuple[bool, str]:
    content = " ".join(content.split())[:MAX_CONTENT]
    if not content:
        return False, "提醒内容不能为空"
    due_ts = int(due.timestamp())
    now_ts = int(time.time())
    if due_ts - now_ts < MIN_AHEAD_SECONDS:
        return False, "提醒时间至少要在 1 分钟以后"
    if due_ts - now_ts > MAX_AHEAD_DAYS * 86400:
        return False, "提醒最多只能设到一年以内"
    store = get_store()
    if len(await store.list_reminders(user_id)) >= MAX_PER_USER:
        return False, f"你已经有 {MAX_PER_USER} 条未触发的提醒了，先取消几条再设"
    rid = await store.add_reminder(user_id, group_id, due_ts, content)
    return True, f"好的，#{rid} 将在 {_fmt(due_ts)} 提醒你：{content}"


async def list_text(user_id: int) -> str:
    items = await get_store().list_reminders(user_id)
    if not items:
        return "你现在没有待触发的提醒。"
    return "你的提醒：\n" + "\n".join(f"#{r['id']} {_fmt(r['due_ts'])} {r['content']}" for r in items)


async def cancel(user_id: int, rid: int) -> tuple[bool, str]:
    ok = await get_store().delete_reminder(rid, user_id)
    return ok, f"已取消提醒 #{rid}" if ok else f"没有找到你的提醒 #{rid}"


# ---------------------------------------------------------------- 指令

remind_cmd = on_command("提醒", aliases={"remind"}, rule=BASIC, priority=5, block=True)
list_cmd = on_command("提醒列表", aliases={"我的提醒"}, rule=BASIC, priority=4, block=True)
cancel_cmd = on_command("取消提醒", rule=BASIC, priority=4, block=True)


def _group_of(event: MessageEvent) -> int | None:
    return event.group_id if isinstance(event, GroupMessageEvent) else None


@remind_cmd.handle()
async def handle_remind(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    parsed = parse_when(text, datetime.now()) if text else None
    if not parsed:
        await matcher.finish("用法：/提醒 <时间> <内容>\n例：/提醒 10分钟后 喝水　/提醒 明天8点 开会　/提醒 20:30 打卡")
    due, content = parsed
    _, msg = await create(event.user_id, _group_of(event), due, content)
    await matcher.send(msg)


@list_cmd.handle()
async def handle_list(matcher: Matcher, event: MessageEvent) -> None:
    await matcher.send(await list_text(event.user_id))


@cancel_cmd.handle()
async def handle_cancel(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip().lstrip("#")
    if not arg.isdigit():
        await matcher.finish("用法：/取消提醒 <编号>（编号见 /提醒列表）")
    _, msg = await cancel(event.user_id, int(arg))
    await matcher.send(msg)


# ---------------------------------------------------------------- 调度

async def _deliver(bot: Bot, r: dict, now_ts: int) -> bool:
    late = now_ts - r["due_ts"] > LATE_SECONDS
    text = f"⏰ 提醒{'（迟到了）' if late else ''}：{r['content']}"
    try:
        if r["group_id"]:
            await bot.call_api("send_group_msg", group_id=r["group_id"],
                               message=MessageSegment.at(r["user_id"]) + " " + text)
        else:
            await bot.call_api("send_private_msg", user_id=r["user_id"], message=text)
        return True
    except Exception:
        logger.exception(f"提醒 #{r['id']} 发送失败")
        return False


async def tick(bot: Bot, now_ts: int | None = None) -> int:
    now_ts = int(now_ts if now_ts is not None else time.time())
    store = get_store()
    n = 0
    for r in await store.due_reminders(now_ts):
        await _deliver(bot, r, now_ts)  # 发不出去也删除，避免永远重发（群已退出 / 好友关系变化）
        await store.delete_reminder(r["id"])
        n += 1
    return n


async def _worker() -> None:
    while True:
        await asyncio.sleep(SCAN_SECONDS)
        try:
            for bot in list(get_bots().values()):
                if isinstance(bot, Bot):
                    await tick(bot)
                    break
        except Exception:
            logger.exception("提醒扫描异常")


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())
