"""AI 问答：@机器人 / 昵称唤起时调用 LLM（OpenAI 兼容接口，如 B.AI）回答。

- 触发：to_me 且非指令的群消息（超管私聊对话也可用）
- 上下文：每群保留最近 N 轮会话（内存），/ai clear 可清空
- 护栏：每群 / 每人每日调用上限（面板可配），超限静默忽略
- 配置：base_url / api_key 来自环境变量；开关、模型、系统提示词、限额存 SQLite kv
  （面板「AI」页可配置）。base_url 或 api_key 缺失时功能自动禁用并打日志。
- 非流式调用，回复超长截断（QQ 消息限长）
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import SUPERUSER
from src.store import get_store

# ---------------------------------------------------------------- 配置（kv）

_KV_KEYS = ("ai_enabled", "ai_model", "ai_system_prompt", "ai_ctx_rounds", "ai_limit_group", "ai_limit_user")

DEFAULTS: dict[str, Any] = {
    "ai_enabled": "true",
    "ai_model": "deepseek-v4-flash",
    "ai_system_prompt": (
        "你是「星潮」，一个开源的 QQ 群助手机器人（官网 https://xingchao.dev）。"
        "回答简洁、友好、口语化，避免长篇大论；不懂就说不懂，不要编造。"
    ),
    "ai_ctx_rounds": "5",
    "ai_limit_group": "100",
    "ai_limit_user": "20",
}

_client: Any = None
_client_unusable: bool = False


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    if raw is None:
        raw = DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


async def ai_config() -> dict[str, Any]:
    return {k: await _kv(k) for k in _KV_KEYS}


def is_configured() -> bool:
    cfg = get_config()
    return bool(cfg.xingchao_ai_base_url) and bool(cfg.xingchao_ai_api_key)


def _get_client() -> Any | None:
    global _client, _client_unusable
    if not is_configured():
        return None
    if _client is None and not _client_unusable:
        try:
            from openai import AsyncOpenAI

            cfg = get_config()
            _client = AsyncOpenAI(
                api_key=cfg.xingchao_ai_api_key, base_url=cfg.xingchao_ai_base_url
            )
        except Exception:
            logger.exception("初始化 OpenAI 客户端失败，AI 功能禁用")
            _client_unusable = True
            return None
    return _client


async def is_ai_enabled() -> bool:
    if not is_configured():
        return False
    return await _kv("ai_enabled") is True


# ---------------------------------------------------------------- 上下文

_ctx: dict[int, list[dict[str, str]]] = {}


def _get_history(group_key: int) -> list[dict[str, str]]:
    return _ctx.setdefault(group_key, [])


def _append_history(group_key: int, role: str, content: str, rounds: int) -> None:
    hist = _get_history(group_key)
    hist.append({"role": role, "content": content})
    # 每轮 = user + assistant 两条，保留最近 rounds 轮
    max_len = max(2, rounds * 2)
    while len(hist) > max_len:
        hist.pop(0)


# ---------------------------------------------------------------- 限额

async def _check_quota(event: MessageEvent, group_limit: int, user_limit: int) -> tuple[bool, str]:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"ai_usage_{day}"
    raw = await store.get_kv(key)
    try:
        usage: dict[str, Any] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    gid = str(getattr(event, "group_id", "private"))
    uid = str(event.user_id)
    groups: dict[str, int] = usage.get("groups", {})
    users: dict[str, int] = usage.get("users", {})
    if groups.get(gid, 0) >= group_limit:
        return False, "本群今日 AI 次数已用完"
    if users.get(uid, 0) >= user_limit:
        return False, "你今日的 AI 次数已用完"
    groups[gid] = groups.get(gid, 0) + 1
    users[uid] = users.get(uid, 0) + 1
    usage["groups"] = groups
    usage["users"] = users
    await store.set_kv(key, json.dumps(usage, ensure_ascii=False))
    return True, ""


async def _usage_payload() -> dict[str, Any]:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"ai_usage_{day}")
    try:
        usage = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    return {"day": day, "groups": usage.get("groups", {}), "users": usage.get("users", {})}


# ---------------------------------------------------------------- 核心问答

MAX_REPLY_CHARS = 1500


async def chat(event: MessageEvent, text: str) -> str | None:
    """调用 LLM；返回回复文本，失败/被拦截返回 None（调用方静默处理）。"""
    cfg = await ai_config()
    client = _get_client()
    if client is None:
        return None

    ok, reason = await _check_quota(event, cfg["ai_limit_group"], cfg["ai_limit_user"])
    if not ok:
        logger.info(f"AI 调用被限额拦截：{reason}")
        return None

    group_key = getattr(event, "group_id", 0) or -int(event.user_id)
    history = _get_history(group_key)
    messages = [{"role": "system", "content": cfg["ai_system_prompt"]}, *history,
                {"role": "user", "content": text}]

    try:
        completion = await client.chat.completions.create(
            model=cfg["ai_model"], messages=messages, temperature=0.7
        )
        reply = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("AI 调用失败")
        return None
    if not reply:
        return None

    _append_history(group_key, "user", text, cfg["ai_ctx_rounds"])
    _append_history(group_key, "assistant", reply, cfg["ai_ctx_rounds"])
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "\n…（内容过长已截断）"
    return reply


# ---------------------------------------------------------------- 消息入口

ai_matcher = on_message(priority=11, block=False)


def _is_command(text: str) -> bool:
    start = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in start)


@ai_matcher.handle()
async def handle_ai(event: MessageEvent, matcher: Matcher) -> None:
    if not event.to_me:
        return
    text = event.message.extract_plain_text().strip()
    if not text or _is_command(text):
        return
    if not await is_ai_enabled():
        return  # mention 插件已处理固定回应
    # 群聊：已在白名单（规则）；私聊：仅超管（mention 层已放行超管私聊）
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent

    if isinstance(event, PrivateMessageEvent) and event.user_id not in {
        int(u) for u in get_driver().config.superusers
    }:
        return

    reply = await chat(event, text)
    if reply is None:
        return
    try:
        if isinstance(event, GroupMessageEvent):
            await matcher.send(
                MessageSegment.at(event.user_id) + " " + reply
            )
        else:
            await matcher.send(reply)
    except MatcherException:
        raise
    except Exception:
        logger.exception("AI 回复发送失败")


# ---------------------------------------------------------------- /ai 指令

ai_cmd = on_command("ai", rule=SUPERUSER, priority=1, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


@ai_cmd.handle()
async def handle_ai_cmd(matcher: Matcher, args: Message = CommandArg()) -> None:
    from src.plugins import mention as mention_plugin

    raw = args.extract_plain_text().strip()
    action, _, rest = raw.partition(" ")
    if action == "on" or action == "off":
        if not is_configured():
            await _send(ai_cmd, "AI 未配置 API 地址或密钥（XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY），无法开启。")
            return
        await get_store().set_kv("ai_enabled", "true" if action == "on" else "false")
        await _send(ai_cmd, f"AI 问答已{'开启' if action == 'on' else '关闭'}。")
        return
    if action == "status":
        cfg = await ai_config()
        usage = await _usage_payload()
        lines = [
            f"AI 问答：{'开启' if await is_ai_enabled() else '关闭'}"
            f"（{'已配置' if is_configured() else '未配置 API'}）",
            f"模型: {cfg['ai_model']}",
            f"上下文: 每群 {cfg['ai_ctx_rounds']} 轮",
            f"今日限额: 每群 {cfg['ai_limit_group']} 次 / 每人 {cfg['ai_limit_user']} 次",
            f"今日用量: 群 {usage['groups']} | 人 {usage['users']}",
        ]
        await _send(ai_cmd, "\n".join(lines))
        return
    if action == "clear":
        gid = getattr(matcher.event, "group_id", None)
        if gid and _ctx.pop(gid, None) is not None:
            await _send(ai_cmd, "本群 AI 会话上下文已清空。")
        else:
            await _send(ai_cmd, "本群没有正在进行的 AI 会话。")
        return
    if action == "test" and rest.strip():
        cfg = await ai_config()
        client = _get_client()
        if client is None:
            await _send(ai_cmd, "AI 未配置（XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY）。")
            return
        try:
            completion = await client.chat.completions.create(
                model=cfg["ai_model"],
                messages=[{"role": "user", "content": rest.strip()}],
            )
            reply = (completion.choices[0].message.content or "").strip()
            await _send(ai_cmd, f"模型回复：\n{reply or '（空）'}")
        except Exception as e:
            logger.exception("AI 测试调用失败")
            await _send(ai_cmd, f"调用失败：{e}")
        return
    await _send(
        ai_cmd,
        "用法：/ai on|off、/ai status、/ai clear、/ai test <问题>\n"
        "（配置修改请在 Web 管理面板「AI」页操作）",
    )


# ---------------------------------------------------------------- 启动提示

driver = get_driver()


@driver.on_startup
async def _startup_hint() -> None:
    if is_configured():
        cfg = await ai_config()
        logger.info(f"AI 问答就绪：模型 {cfg['ai_model']}（默认{'开启' if cfg['ai_enabled'] else '关闭'}）")
    else:
        logger.warning(
            "AI 问答未启用：缺少 XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY。"
            "配置后并在面板「AI」页或 /ai on 开启即可使用。"
        )
