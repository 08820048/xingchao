"""新人入群验证：新成员入群后要在限定时间内回答一道简单算术题，防广告号 / 机器号。

- 默认关闭。/入群验证 开|关；/入群验证 提醒|踢出（超时处理方式）；/入群验证 <分钟>（时限，1–60，默认 5）
  AI 工具：set_join_verify（超管）
- 流程：白名单群有人入群 → 群里 @ 新人出题（如 "3+8=?"）→ 新人在群里发出正确数字即通过 →
  超时未通过：
    * 提醒（默认）：私聊通知超管，由人决定
    * 踢出：直接移出群聊（需机器人是群管理员；失败会通知超管）
- 不验证：机器人自己、超管；入群时已有的老成员不受影响
- 待验证名单只在内存里：机器人重启后，重启前入群、尚未验证的人不再追踪
- kv：joinverify_enabled / joinverify_action（notify|kick）/ joinverify_minutes
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass

from nonebot import on_command, on_message, on_notice
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import superuser_ids
from src.background import periodic
from src.permission import GROUP_WHITELIST, SUPERUSER
from src.store import get_store

DEFAULT_MINUTES = 5
SCAN_SECONDS = 15

_NUM = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


@dataclass
class Pending:
    answer: int
    deadline: float
    question: str


_pending: dict[tuple[int, int], Pending] = {}


def make_question(rng: random.Random | None = None) -> tuple[str, int]:
    rng = rng or random
    a, b = rng.randint(2, 9), rng.randint(2, 9)
    if rng.random() < 0.5:
        return f"{a}+{b}", a + b
    a, b = max(a, b), min(a, b)
    return f"{a}-{b}", a - b


def check_answer(text: str, answer: int) -> bool:
    """消息里出现的独立数字中包含答案即通过（容忍"答案是 11"这类写法）。"""
    return any(int(n) == answer for n in _NUM.findall(text))


def expired(now: float) -> list[tuple[int, int]]:
    return [k for k, p in _pending.items() if p.deadline <= now]


async def _cfg() -> dict:
    store = get_store()
    try:
        minutes = int(await store.get_kv("joinverify_minutes") or DEFAULT_MINUTES)
    except ValueError:
        minutes = DEFAULT_MINUTES
    return {
        "enabled": (await store.get_kv("joinverify_enabled")) == "true",
        "action": "kick" if (await store.get_kv("joinverify_action")) == "kick" else "notify",
        "minutes": min(60, max(1, minutes)),
    }


async def set_config(enabled: bool | None = None, action: str | None = None, minutes: int | None = None) -> str:
    store = get_store()
    if enabled is not None:
        await store.set_kv("joinverify_enabled", "true" if enabled else "false")
    if action in ("notify", "kick"):
        await store.set_kv("joinverify_action", action)
    if minutes is not None:
        await store.set_kv("joinverify_minutes", str(min(60, max(1, int(minutes)))))
    c = await _cfg()
    return (f"入群验证：{'开启' if c['enabled'] else '关闭'}｜超时处理：{'踢出' if c['action'] == 'kick' else '仅提醒超管'}"
            f"｜时限 {c['minutes']} 分钟")


# ---------------------------------------------------------------- 事件

join_notice = on_notice(rule=GROUP_WHITELIST, priority=4, block=False)


@join_notice.handle()
async def handle_join(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    uid = event.user_id
    if uid == int(bot.self_id) or uid in superuser_ids():
        return
    cfg = await _cfg()
    if not cfg["enabled"]:
        return
    q, ans = make_question()
    _pending[(event.group_id, uid)] = Pending(ans, time.time() + cfg["minutes"] * 60, q)
    tail = "超时会被请出群聊" if cfg["action"] == "kick" else "超时会通知管理员"
    try:
        await bot.call_api(
            "send_group_msg", group_id=event.group_id,
            message=MessageSegment.at(uid) + f" 欢迎！为防广告号，请在 {cfg['minutes']} 分钟内回复 {q} 的结果（发数字即可），{tail}。",
        )
    except Exception:
        logger.exception("发送入群验证题失败")


verify_msg = on_message(rule=GROUP_WHITELIST, priority=3, block=False)


@verify_msg.handle()
async def handle_answer(bot: Bot, event: GroupMessageEvent) -> None:
    key = (event.group_id, event.user_id)
    p = _pending.get(key)
    if p is None:
        return
    if check_answer(event.message.extract_plain_text(), p.answer):
        _pending.pop(key, None)
        try:
            await bot.call_api("send_group_msg", group_id=event.group_id,
                               message=MessageSegment.at(event.user_id) + " 验证通过，玩得开心～")
        except Exception:
            logger.debug("发送验证通过提示失败", exc_info=True)


# ---------------------------------------------------------------- 超时处理

async def handle_timeouts(bot: Bot, now: float | None = None) -> int:
    from src.plugins.appeals import notify_superusers

    now = time.time() if now is None else now
    keys = expired(now)
    if not keys:
        return 0
    action = (await _cfg())["action"]
    for gid, uid in keys:
        _pending.pop((gid, uid), None)
        if action == "kick":
            try:
                await bot.call_api("set_group_kick", group_id=gid, user_id=uid, reject_add_request=False)
                await notify_superusers(bot, f"🧹 入群验证超时：已将 {uid} 移出群 {gid}")
            except Exception as e:
                await notify_superusers(
                    bot, f"⚠️ 入群验证超时：{uid}（群 {gid}）未通过，但踢出失败：{str(getattr(e, 'info', None) or e)[:60]}")
        else:
            await notify_superusers(bot, f"⚠️ 入群验证超时：{uid}（群 {gid}）未回答验证题，请管理员决定是否处理")
    return len(keys)


@periodic("入群验证超时扫描", SCAN_SECONDS)
async def _scan(bot: Bot) -> None:
    await handle_timeouts(bot)


# ---------------------------------------------------------------- 指令

verify_cmd = on_command("入群验证", rule=SUPERUSER, priority=1, block=True)


@verify_cmd.handle()
async def handle_cmd(matcher: Matcher, args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip()
    if arg in ("开", "on"):
        await matcher.finish(await set_config(enabled=True))
    if arg in ("关", "off"):
        await matcher.finish(await set_config(enabled=False))
    if arg in ("提醒", "notify"):
        await matcher.finish(await set_config(action="notify"))
    if arg in ("踢出", "kick"):
        await matcher.finish(await set_config(action="kick"))
    if arg.isdigit():
        await matcher.finish(await set_config(minutes=int(arg)))
    await matcher.finish((await set_config()) + "\n用法：/入群验证 开|关｜提醒|踢出｜<分钟>")
