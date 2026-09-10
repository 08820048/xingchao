"""群聊主动性：当群里正在讨论明确话题或有人求助/提问时，让 AI 自然加入讨论。

设计目标：既要有“眼力见”，又要把模型消耗压到最低。

- 只监听白名单群的**非指令、非 @机器人** 文本消息（这些已由 AI 问答处理）
- 两段式控本：
  1) 规则门槛：维护每群最近消息滑动窗口，只有当窗口内消息数、发言人数达标，
     或最近消息里出现求助/提问信号时，才考虑调用模型；
  2) 单次判定：交给模型判断“是否值得插话”，不值得则输出 SKIP，不发送任何消息。
- 限流：proactive_cooldown 秒内每群最多尝试一次；每群每日最多 proactive_daily_limit 次尝试
  （尝试即计次，避免模型反复空转）。
- 开关 proactive_enabled（面板模块开关 / /plugin proactive on|off）
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.markdown import md_to_qq
from src.permission import GROUP_WHITELIST
from src.store import get_store

DEFAULTS: dict[str, object] = {
    "proactive_enabled": True,
    "proactive_cooldown": 300,        # 每群两次尝试的最小间隔（秒）
    "proactive_daily_limit": 30,      # 每群每日尝试上限（含 SKIP）
    "proactive_window": 300,          # 滑动窗口（秒）
    "proactive_min_messages": 5,      # 非求助场景：窗口内至少这么多条消息
    "proactive_min_users": 3,         # 非求助场景：至少这么多不同发言人
    "proactive_help_min_messages": 2,  # 求助场景：窗口内至少这么多条消息
    "proactive_max_reply_chars": 120,  # 回复硬上限
    "proactive_context_messages": 12,  # 送入模型的最大消息条数
}

# 求助 / 提问信号
_HELP_RE = re.compile(
    r"(怎么|如何|为什么|为何|咋办|怎么办|请教|求助|求解|请问|求推荐|帮我|"
    r"有没有人|有没有大佬|谁(会|懂|知道|了解)|在线等|急问|不懂就问|大佬们)",
    re.IGNORECASE,
)
_SKIP_RE = re.compile(r"^\W*skip\b", re.IGNORECASE)
_MAX_BUFFER = 40

_buffer: dict[int, list[dict[str, Any]]] = {}
_last_attempt: dict[int, float] = {}


# ---------------------------------------------------------------- 配置

async def _kv(key: str):
    raw = await get_store().get_kv(key)
    if raw is None:
        raw = DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


async def proactive_enabled() -> bool:
    return await _kv("proactive_enabled") is True


async def _bump_usage(group_id: int) -> None:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"proactive_usage_{day}"
    raw = await store.get_kv(key)
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    usage[str(group_id)] = usage.get(str(group_id), 0) + 1
    await store.set_kv(key, json.dumps(usage, ensure_ascii=False))


async def _daily_count(group_id: int) -> int:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"proactive_usage_{day}")
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return 0
    return int(usage.get(str(group_id), 0))


# ---------------------------------------------------------------- 判定

def _is_command(text: str) -> bool:
    starts = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in starts)


def _is_question(text: str) -> bool:
    text = text.strip()
    return len(text) >= 5 and text[-1] in "?？"


def _looks_like_help(text: str) -> bool:
    return bool(_HELP_RE.search(text)) or _is_question(text)


def _sender_name(event: GroupMessageEvent) -> str:
    sender = event.sender
    return (getattr(sender, "card", None) or getattr(sender, "nickname", None)
            or str(event.user_id))


def _build_prompt(messages: list[dict[str, Any]], help_hit: bool) -> str:
    transcript = "\n".join(
        f"{m['name']}: {m['text'][:100]}" for m in messages
    )
    prompt = (
        "下面是 QQ 群最近的聊天记录：\n"
        "----\n"
        f"{transcript}\n"
        "----\n"
        "你是群里的机器人「星潮」。请判断大家是否正在讨论一个明确的话题，"
        "或有人在求助/提问。\n"
        "如果是，用自然、简短的群聊口吻插一句话参与讨论或给出有帮助的回答（不超过 60 字）；"
        "如果只是随口闲聊、话题不明确、或没必要插话，只输出 SKIP。\n"
        "要求：不要 @ 任何人，不要用 Markdown，不要暴露自己是 AI，不要重复别人的话。\n"
        "只输出要发送的内容，或 SKIP。"
    )
    if help_hit:
        prompt += "\n（提示：最近消息里似乎有人在求助或提问，如能给出有用回答就回答，否则 SKIP。）"
    return prompt


# ---------------------------------------------------------------- 处理

proactive_matcher = on_message(rule=GROUP_WHITELIST, priority=12, block=False)


@proactive_matcher.handle()
async def handle_proactive(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id) or event.to_me:
        return
    text = event.message.extract_plain_text().strip()
    if not text or _is_command(text):
        return
    if not await proactive_enabled():
        return

    from src.plugins import ai as ai_plugin

    if not await ai_plugin.is_ai_enabled():
        return

    now = time.monotonic()
    window = int(await _kv("proactive_window"))
    buf = _buffer.setdefault(event.group_id, [])
    buf[:] = [m for m in buf if now - m["ts"] <= window]
    buf.append({
        "ts": now,
        "user_id": event.user_id,
        "name": _sender_name(event),
        "text": text,
        "message_id": event.message_id,
        "help": _looks_like_help(text),
    })
    if len(buf) > _MAX_BUFFER:
        del buf[: len(buf) - _MAX_BUFFER]

    # 限流：冷却 + 每日尝试上限
    if now - _last_attempt.get(event.group_id, 0.0) < int(await _kv("proactive_cooldown")):
        return
    if await _daily_count(event.group_id) >= int(await _kv("proactive_daily_limit")):
        return

    recent = buf[-3:]
    help_hit = any(m["help"] for m in recent)
    min_messages = int(await _kv("proactive_help_min_messages")) if help_hit \
        else int(await _kv("proactive_min_messages"))
    min_users = 1 if help_hit else int(await _kv("proactive_min_users"))
    users = {m["user_id"] for m in buf}
    if len(buf) < min_messages or len(users) < min_users:
        return

    # 通过门槛：记一次尝试（无论最终是否发送，都计入消耗）
    _last_attempt[event.group_id] = now
    await _bump_usage(event.group_id)

    context_n = int(await _kv("proactive_context_messages"))
    reply = await ai_plugin.generate_text(
        _build_prompt(buf[-context_n:], help_hit),
        system="你是 QQ 群机器人星潮，聊天自然、简短、有分寸，不刷屏、不硬插话。",
        temperature=0.8,
    )
    if not reply or _SKIP_RE.match(reply.strip()):
        logger.debug(f"群 {event.group_id} 主动性：模型判定 SKIP 或生成失败")
        return

    reply = md_to_qq(reply).strip().replace("\n", " ")
    max_chars = int(await _kv("proactive_max_reply_chars"))
    if len(reply) > max_chars:
        reply = reply[:max_chars]

    message: MessageSegment | str = reply
    if help_hit:
        target = next((m for m in reversed(buf) if m.get("help")), buf[-1])
        message = MessageSegment.reply(target["message_id"]) + reply
    try:
        await matcher.send(message)
    except MatcherException:
        raise
    except Exception:
        logger.exception("主动发言发送失败")
