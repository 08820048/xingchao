"""新人入群验证：新成员入群后要在限定时间内回答一道简单算术题，防广告号 / 机器号。

- 默认关闭。/入群验证 开|关；/入群验证 提醒|踢出（超时处理方式）；/入群验证 <分钟>（时限，1–60，默认 5）
  AI 工具：set_join_verify（超管）
- 流程：白名单群有人入群 → 群里 @ 新人出题（如 "3+8=?"）→ 新人在群里发出正确数字即通过
- 未通过有两种情形：超时没回答；或答错次数用完（发了数字但不对，最多 3 次，每次错都会 @ 提示剩余机会）
- 处罚前先公示：判定未通过后，机器人先在群里 @ 该成员说明原因和即将执行的处理，
  再给 30 秒宽限（期间答对仍可通过），到点才执行：
    * 提醒（默认）：私聊通知超管，由人决定
    * 踢出：移出群聊（需机器人是群管理员；成功后群里再说明一句，失败会通知超管）
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
SCAN_SECONDS = 5
MAX_ATTEMPTS = 3    # 答错几次算未通过
GRACE_SECONDS = 30  # 群里公示后到真正处罚之间的宽限

_NUM = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


@dataclass
class Pending:
    answer: int
    deadline: float
    question: str
    attempts: int = 0               # 已答错次数
    punish_at: float | None = None  # 已在群里公示未通过，到此时刻执行处罚
    reason: str = ""


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


def is_wrong_attempt(text: str, answer: int) -> bool:
    """发了数字但没有一个是答案：算一次答错（纯聊天没有数字则不算）。"""
    return bool(_NUM.findall(text)) and not check_answer(text, answer)


def due_for_warning(now: float) -> list[tuple[int, int]]:
    """超时、且还没公示过的人。"""
    return [k for k, p in _pending.items() if p.punish_at is None and p.deadline <= now]


def due_for_punish(now: float) -> list[tuple[int, int]]:
    """已公示、宽限已过的人。"""
    return [k for k, p in _pending.items() if p.punish_at is not None and p.punish_at <= now]


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
    tail = "未通过会被请出群聊" if cfg["action"] == "kick" else "未通过会通知管理员"
    try:
        await bot.call_api(
            "send_group_msg", group_id=event.group_id,
            message=MessageSegment.at(uid) + f" 欢迎！为防广告号，请在 {cfg['minutes']} 分钟内回复 {q} 的结果（发数字即可，共 {MAX_ATTEMPTS} 次作答机会），{tail}。",
        )
    except Exception:
        logger.exception("发送入群验证题失败")


verify_msg = on_message(rule=GROUP_WHITELIST, priority=3, block=False)


async def _say(bot: Bot, group_id: int, user_id: int, text: str) -> None:
    try:
        await bot.call_api("send_group_msg", group_id=group_id,
                           message=MessageSegment.at(user_id) + " " + text)
    except Exception:
        logger.exception("入群验证：群内通知发送失败")


async def _say_plain(bot: Bot, group_id: int, text: str) -> None:
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=text)
    except Exception:
        logger.exception("入群验证：群内通知发送失败")


@verify_msg.handle()
async def handle_answer(bot: Bot, event: GroupMessageEvent) -> None:
    key = (event.group_id, event.user_id)
    p = _pending.get(key)
    if p is None:
        return
    text = event.message.extract_plain_text()
    if check_answer(text, p.answer):  # 宽限期内答对同样通过
        _pending.pop(key, None)
        await _say(bot, event.group_id, event.user_id, "验证通过，玩得开心～")
        return
    if p.punish_at is None and is_wrong_attempt(text, p.answer):
        p.attempts += 1
        if p.attempts >= MAX_ATTEMPTS:
            await announce_failure(bot, key, f"答错了 {MAX_ATTEMPTS} 次")
        else:
            await _say(bot, event.group_id, event.user_id,
                       f"答案不对哦，请再算一次 {p.question}（还有 {MAX_ATTEMPTS - p.attempts} 次机会）")


# ---------------------------------------------------------------- 未通过：先公示，再处罚

async def announce_failure(bot: Bot, key: tuple[int, int], reason: str, now: float | None = None) -> None:
    """判定未通过：在群里 @ 该成员说明原因和即将执行的处理，并开始宽限倒计时。"""
    p = _pending.get(key)
    if p is None or p.punish_at is not None:
        return
    now = time.time() if now is None else now
    p.punish_at = now + GRACE_SECONDS
    p.reason = reason
    gid, uid = key
    what = "被移出群聊" if (await _cfg())["action"] == "kick" else "交由管理员处理"
    await _say(bot, gid, uid,
               f"入群验证未通过（{reason}）。{GRACE_SECONDS} 秒后将{what}；"
               f"现在回复 {p.question} 的结果还来得及。")


async def punish(bot: Bot, key: tuple[int, int], action: str) -> None:
    from src.plugins.appeals import notify_superusers

    p = _pending.pop(key, None)
    gid, uid = key
    reason = p.reason if p else "未通过"
    if action == "kick":
        try:
            await bot.call_api("set_group_kick", group_id=gid, user_id=uid, reject_add_request=False)
        except Exception as e:
            await _say(bot, gid, uid, "入群验证未通过，但我没有权限移出你，已通知管理员处理。")
            await notify_superusers(
                bot, f"⚠️ 入群验证未通过：{uid}（群 {gid}，{reason}），但踢出失败：{str(getattr(e, 'info', None) or e)[:60]}")
            return
        await _say_plain(bot, gid, f"{uid} 入群验证未通过（{reason}），已移出群聊。")
        await notify_superusers(bot, f"🧹 入群验证未通过：已将 {uid} 移出群 {gid}（{reason}）")
    else:
        await notify_superusers(bot, f"⚠️ 入群验证未通过：{uid}（群 {gid}，{reason}），请管理员决定是否处理")


async def handle_timeouts(bot: Bot, now: float | None = None) -> int:
    """每次扫描：超时的先公示；已公示且宽限已过的执行处罚。返回本次处理的人数。"""
    now = time.time() if now is None else now
    warn, punish_keys = due_for_warning(now), due_for_punish(now)
    if not (warn or punish_keys):
        return 0
    for key in warn:
        await announce_failure(bot, key, "超时未回答", now)
    if punish_keys:
        action = (await _cfg())["action"]
        for key in punish_keys:
            await punish(bot, key, action)
    return len(warn) + len(punish_keys)


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
