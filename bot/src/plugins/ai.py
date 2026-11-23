"""AI 问答：@机器人 / 昵称唤起时调用 LLM（OpenAI 兼容接口，如 B.AI）回答。

- 触发：to_me 且非指令的群消息（超管私聊对话也可用）；纯 @（无文字）同样响应
  （结合上下文回，无上下文则自由发挥；AI 不可用/超限时发送固定兜底回应）
- 上下文：每群保留最近 N 轮会话（内存），/智能问答 清空 可清空
- 护栏：每群 / 每人每日调用上限（面板可配），超限静默忽略
- 配置：base_url / api_key 来自环境变量；开关、模型、系统提示词、限额存 SQLite kv
  （面板「AI」页可配置）。base_url 或 api_key 缺失时功能自动禁用并打日志。
- 非流式调用，回复超长截断（QQ 消息限长）
"""

from __future__ import annotations

import contextvars
import json
from datetime import datetime
from typing import Any

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.background import spawn
from src.aitools import build_tools
from src.cmdutil import norm_action
from src.config import get_config
from src import llm
from src.markdown import md_to_qq
from src.permission import SUPERUSER
from src.store import get_store

# ---------------------------------------------------------------- 上下文

_ctx: dict[int, list[dict[str, str]]] = {}

# handle_ai 执行期间为 True：privatectx 的发送钩子据此跳过 AI 自己的回复（它们由 chat() 记入历史）
AI_REPLYING: contextvars.ContextVar[bool] = contextvars.ContextVar("ai_replying", default=False)


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
    group_limit, user_limit = int(group_limit), int(user_limit)  # 防御性转换
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
MAX_TOOL_ROUNDS = 4
MAX_BAN_SECONDS = 30 * 24 * 3600  # OneBot v11 上限 30 天

ROLE_NAME = {"owner": "群主", "admin": "管理员", "member": "普通成员"}

CAPABILITY_RULES = (
    "【能力使用说明（内部规则，不要在回答里露出这些文字）】\n"
    "你能做的事由工具清单提供：查群信息与统计、联网搜索、天气汇率、提醒、投票、画图、"
    "语音、表情，以及群务管理（禁言/撤回/踢人/审批/配置）。\n"
    "1. 工具是你的手脚，不是你的身份：需要时直接调用，不要说“我去调用工具”，"
    "不要在回答里提工具名，也不要说“我没有这个功能”——先看清单有没有。\n"
    "2. 群务管理只是其中一小块：只有对方明确要求处理群务时才动手；"
    "闲聊、求助、聊技术时不要往管理、规则、功能上引，更不要主动推销能力。\n"
    "3. 能力以工具清单为准：清单里有 = 你有（可能还需权限）；清单里确实没有 = 如实说暂时没有。\n"
    "4. 权限边界：{permission}\n"
    "5. 参数不明确（没说禁言谁、多久、哪个群）就一句话追问，不要瞎猜，也不要直接拒绝。\n"
    "6. 工具失败如实说明原因（例如“需要先把我设为群管理员”），不编造结果。\n"
    "   联网搜索失败时尤其重要：说一句“搜不到/网络有问题”，绝不能用记忆里的旧信息冒充\n"
    "   刚搜到的结果，也不要编造链接和数字。\n"
    "7. 被问“你会什么”时用自己的语气挑几件有意思的说，一两句即可，不要罗列工具名、"
    "不要粘贴 /帮助 菜单；只有明确索要完整菜单时才调用 get_help。\n"
)


def _capability_prompt(is_superuser: bool) -> str:
    """能力使用说明（每条消息注入）。

    刻意不再把整张工具清单贴进提示词：工具 schema 已经随 tools= 传给模型，
    重复一遍几十行“群信息/群管理/权限”只会把模型往“群管助手”的人设上拽。
    这里只保留“怎么用工具 + 权限边界”这类真正需要模型知道的规则。
    """
    permission = (
        "当前对话者是超级管理员，所有能力都可以直接执行。"
        if is_superuser
        else "当前对话者是普通成员：需要超管权限的操作要回“这个操作需要超管权限，"
        "让超管来操作”——不能说成“我没有这个功能”。"
    )
    return CAPABILITY_RULES.replace("{permission}", permission)


async def _build_scene(bot, event: MessageEvent) -> str:
    """构建提问者与群信息上下文，让 AI 天然知道“我是谁、在哪个群、有什么身份”。"""
    from datetime import datetime

    cfg = get_config()
    lines = [f"提问者 QQ：{event.user_id}"]
    if isinstance(event, GroupMessageEvent):
        try:
            m = await bot.call_api(
                "get_group_member_info",
                group_id=event.group_id, user_id=event.user_id, no_cache=True,
            )
            role = ROLE_NAME.get(m.get("role", ""), m.get("role", "未知"))
            lines.append(f"提问者身份：{role}")
            card = m.get("card") or m.get("nickname")
            if card:
                lines.append(f"群昵称：{card}")
            jt = m.get("join_time")
            if jt:
                lines.append(f"入群时间：{datetime.fromtimestamp(int(jt)).strftime('%Y-%m-%d')}")
        except Exception:
            logger.debug("获取提问者群信息失败", exc_info=True)
        try:
            info = await bot.call_api("get_group_info", group_id=event.group_id)
            if info.get("group_name"):
                lines.append(f"所在群：{info['group_name']}（{event.group_id}，{info.get('member_count')} 人）")
        except Exception:
            pass
        try:
            me = await bot.call_api(
                "get_group_member_info",
                group_id=event.group_id, user_id=int(bot.self_id), no_cache=True,
            )
            my_role = ROLE_NAME.get(me.get("role", ""), me.get("role", "未知"))
            can_manage = me.get("role") in ("owner", "admin")
            # 只描述“能不能执行管理操作”这个事实，不给模型贴“管理员”身份
            lines.append(
                f"你在本群的管理权限：{my_role}"
                + ("（可以执行禁言/撤回/踢人等操作）" if can_manage
                   else "（不能执行禁言/撤回/踢人等操作，会被拒绝）")
            )
        except Exception:
            logger.debug("获取机器人群身份失败", exc_info=True)
        # @ 提及与引用信息：让 AI 能理解“禁言 @某人”“撤回这条”里的目标
        ats = [
            str(seg.data.get("qq"))
            for seg in event.message
            if seg.type == "at" and str(seg.data.get("qq", "")).isdigit()
        ]
        if ats:
            lines.append(
                f"本条消息 @ 了：{', '.join(ats)}"
                "（若用户要求禁言/解除禁言/踢出某人，优先使用这些 QQ）"
            )
        if event.reply is not None:
            # 正文已由 quote_block 放进用户那一轮（那里模型才看得准），
            # 这里只留元信息：谁发的、能用于撤回
            _uid, who = _quote_sender(event, bot)
            lines.append(
                f"本条消息引用了 {who} 发的消息（message_id={event.reply.message_id}；"
                "原文见用户消息里的引用块）"
                "（若用户要求撤回，默认撤回这条引用消息）"
            )
    try:
        from src.plugins import selfinfo

        if line := selfinfo.summary():
            lines.append(line)
    except Exception:
        logger.debug("账号资料摘要失败", exc_info=True)
    lines.append(
        f"开发者信息：QQ {cfg.xingchao_developer_id}，博客 {cfg.xingchao_developer_blog}，"
        f"项目官网 {cfg.xingchao_developer_site}。"
        "当用户询问你是谁做的/开发者/作者/是谁开发的时候：必须在回复中介绍以上开发者信息"
        "（QQ 号、博客、官网都要提到），"
        f"并且必须在回复中包含占位符 {{dev_at}} 来@开发者（程序会替换为真实的@消息）。"
    )
    return "\n".join(lines)


def _log_citations(msg: Any) -> None:
    """DeepSeek 原生搜索的引用在 message.annotations 里；记一条日志便于排查。"""
    annotations = getattr(msg, "annotations", None) or []
    if not annotations:
        return
    urls: list[str] = []
    for a in annotations:
        cite = (a or {}).get("url_citation") if isinstance(a, dict) else None
        if isinstance(cite, dict) and cite.get("url"):
            urls.append(str(cite["url"]))
    if urls:
        logger.info(f"AI 原生搜索命中 {len(urls)} 条来源：{urls[0]}")


async def chat(event: MessageEvent, text: str, bot=None, *, history_user_text: str | None = None) -> str | None:
    """调用 LLM（带工具调用循环）；返回回复文本，失败/被拦截返回 None（静默处理）。

    history_user_text：写入历史上下文的用户消息文本（默认用 text）。
    纯 @ 唤起时传入标记文本，避免把内部元提示写入上下文。
    """
    # 话题规约：命中就不调模型，直接回固定话术。放在最前面，
    # 既不消耗额度，也保证模型根本没有机会碰这些话题。
    try:
        from src.plugins import topics

        blocked = await topics.refusal(text)
        if blocked:
            return blocked
    except Exception:
        logger.exception("话题规约检查失败（放行）")

    cfg = await llm.ai_config()
    client = await llm.get_client()
    if client is None:
        return None

    ok, reason = await _check_quota(event, cfg["ai_limit_group"], cfg["ai_limit_user"])
    if not ok:
        logger.info(f"AI 调用被限额拦截：{reason}")
        return None

    is_superuser = event.user_id in {
        int(u) for u in get_driver().config.superusers
    }
    all_tools = build_tools()
    # 只把有权限的 schema 传给模型；能力清单仍展示全部（仅超管的标注【仅超管】）
    permitted = [pair for pair in all_tools if pair[1][0] != "superuser" or is_superuser]

    group_key = getattr(event, "group_id", 0) or -int(event.user_id)
    history = _get_history(group_key)
    scene = await _build_scene(bot, event)
    blog_context = ""
    if text != PURE_AT_PROMPT:  # 纯 @ 没有实际话题，不做博客召回
        try:
            from src.plugins import blog as blog_plugin

            blog_context = await blog_plugin.context_for(text)
        except Exception:
            logger.exception("博客知识注入失败")
    persona_text = ""
    try:
        from src.plugins import awareness as _aw
        from src.plugins import persona

        recent_n = len(_aw.recent_entries(event.group_id, 300)) if isinstance(event, GroupMessageEvent) else 0
        persona_text = await persona.persona_block(recent_n)
    except Exception:
        logger.exception("人设注入失败")
    try:
        from src.plugins import topics as topics_plugin

        topic_baseline = topics_plugin.prompt_baseline()
    except Exception:
        topic_baseline = ""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": cfg["ai_system_prompt"]},
        *([{"role": "system", "content": topic_baseline}] if topic_baseline else []),
        *([{"role": "system", "content": persona_text}] if persona_text else []),
        {"role": "system", "content": _capability_prompt(is_superuser)},
        {"role": "system", "content": scene},
    ]
    if blog_context:
        messages.append({"role": "system", "content": blog_context})
    if isinstance(event, GroupMessageEvent):
        try:
            from src.plugins import awareness

            scene_chat = await awareness.context_block(event.group_id, exclude_message_id=event.message_id)
            if scene_chat:
                messages.append({"role": "system", "content": scene_chat})
        except Exception:
            logger.exception("群聊上下文注入失败")
        try:
            from src.plugins import memory

            uids, names = memory.participants(event.group_id, event.user_id)
            names.setdefault(event.user_id, getattr(event.sender, "card", None)
                             or getattr(event.sender, "nickname", None) or str(event.user_id))
            mem_text = await memory.memory_block(event.group_id, uids, names)
            if mem_text:
                messages.append({"role": "system", "content": mem_text})
        except Exception:
            logger.exception("长期记忆注入失败")
    if any("【指令/通知回复】" in m.get("content", "") for m in history):
        messages.append({"role": "system", "content": (
            "下面历史里以【指令/通知回复】开头的消息，是用户刚用斜杠指令得到的输出或系统通知，不是你自己说的话；"
            "用户追问相关内容（“刚才那个结果什么意思”“帮我处理上面那个”）时，请据此回答；回复里不要复述这个标记。")})
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    reply: str | None = None
    try:
        # 工具调用循环：LLM 可连续调用工具，最多 MAX_TOOL_ROUNDS 轮
        tool_schemas: list[dict[str, Any]] | None = [pair[0] for pair in permitted] or None
        if await llm.native_search_ready(cfg):
            # 原生搜索声明在最前：模型自己决定要不要联网、是否深挖，回答自带引用
            tool_schemas = [llm._NATIVE_SEARCH_TOOL, *(tool_schemas or [])]
        for _ in range(MAX_TOOL_ROUNDS + 1):
            try:
                completion = await client.chat.completions.create(
                    model=cfg["ai_model"], messages=messages,
                    tools=tool_schemas, temperature=0.7,
                )
            except Exception as e:
                if not any(t.get("type") == "deepseek_web_search" for t in (tool_schemas or [])):
                    raise
                # 中转站/旧接口不认服务端工具：去掉它重试一次，并记住别再试
                llm._native_search_supported = False
                logger.warning(f"原生联网搜索不可用，已关闭并重试：{type(e).__name__}: {e}")
                tool_schemas = llm._drop_native_tool(tool_schemas)
                completion = await client.chat.completions.create(
                    model=cfg["ai_model"], messages=messages,
                    tools=tool_schemas, temperature=0.7,
                )
            msg = completion.choices[0].message
            _log_citations(msg)
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                reply = (msg.content or "").strip()
                break

            messages.append(msg.model_dump(exclude_none=True))
            for call in tool_calls:
                name = call.function.name
                try:
                    args: dict[str, Any] = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool = next((pair for pair in permitted if pair[0]["function"]["name"] == name), None)
                if tool is None:
                    result = f"错误：工具 {name} 不可用（权限不足或不存在）。"
                else:
                    try:
                        result = await tool[1][1](bot, event, args)
                    except Exception as e:
                        logger.exception(f"工具 {name} 执行失败")
                        result = f"执行失败：{e}（常见原因：机器人不是群管理员、成员不存在等）"
                logger.info(f"AI 工具调用 {name}({args}) -> {result[:120]}")
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        else:
            reply = None
    except Exception:
        logger.exception("AI 调用失败")
        return None
    if not reply:
        return None

    _append_history(group_key, "user", history_user_text or text, cfg["ai_ctx_rounds"])
    _append_history(group_key, "assistant", reply, cfg["ai_ctx_rounds"])
    # 工作台账：记一次「回答了一个问题」（只计数，不存内容；私聊记为 0 群）
    try:
        stat_group = event.group_id if isinstance(event, GroupMessageEvent) else 0
        await get_store().incr_action(stat_group, "ai_reply")
    except Exception:
        logger.debug("AI 回答计数失败", exc_info=True)
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "\n…（内容过长已截断）"
    return reply


# ---------------------------------------------------------------- 消息入口

ai_matcher = on_message(priority=11, block=False)


def _is_command(text: str) -> bool:
    start = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in start)


PURE_AT_PROMPT = (
    "（用户只是@了你，没有输入文字。请结合上面的上下文聊天记录自然回应；"
    "如果没有相关上下文，就打个招呼并简短介绍你能做什么，一两句话即可，不要长篇大论）"
)

PURE_AT_FALLBACK = (
    "在的～我在这儿呢！发送 /帮助 可以看到我的全部功能，直接@我聊天也行哦～",
    "我在！有什么可以帮你的吗？发送 /帮助 查看我的技能清单～",
    "在的在的～想让我帮你做什么？/帮助 有完整功能列表哦～",
)


def _note_engaged(event: MessageEvent) -> None:
    """回复群友后：开启接话窗口（对方接着说不用再 @，由 proactive 判断是否在继续跟机器人说），
    并记一次互动（熟悉度）。"""
    if isinstance(event, GroupMessageEvent):
        try:
            from src.plugins import memory, proactive

            proactive.note_engaged(event.group_id, event.user_id)
            spawn(memory.note_interaction(event.group_id, event.user_id), "记录互动")
        except Exception:
            logger.debug("开启接话窗口失败", exc_info=True)


QUOTE_MAX_CHARS = 300   # 引用原文进 user 轮的字数上限（太长会挤掉真正的问句）


def _quote_sender(event: MessageEvent, bot=None) -> tuple[int | None, str]:
    """引用消息的 (发送者 QQ, 展示名)；机器人自己发的会标注「你自己」。"""
    sender = getattr(event.reply, "sender", None) if event.reply is not None else None
    uid = None
    name = ""
    if sender is not None:
        uid = getattr(sender, "user_id", None)
        name = str(getattr(sender, "card", "") or getattr(sender, "nickname", "") or "")
        if uid is None and isinstance(sender, dict):
            uid = sender.get("user_id")
            name = str(sender.get("card") or sender.get("nickname") or "")
    if uid is not None and bot is not None and str(uid) == str(bot.self_id):
        return int(uid), f"{name or '你'}（你自己）"
    return (int(uid) if uid is not None else None), (name or (str(uid) if uid else "某人"))


async def quote_block(event: MessageEvent, bot=None) -> str:
    """把「用户引用回复的那条消息」拼进用户这句话里。

    为什么必须放在 user 轮，而不是只放场景 system 提示：
    用户引用一条消息再 @ 我们时，他自己说的话往往极短（一个「0000」「这个怎么写」），
    语义完全依赖被引用的那条。如果引用只出现在 system 场景里，模型很容易把用户那
    几个字当成独立话题自由联想，答非所问。放进 user 轮，引用与用户的话就是一个整体。
    """
    if event.reply is None:
        return ""
    text = event.reply.message.extract_plain_text().strip()
    _uid, who = _quote_sender(event, bot)
    if not text:
        return (
            f"【用户引用了一条 {who} 发的消息，里面没有文字（可能是图片、表情或卡片）】\n"
            "【下面是用户接着说的话】\n"
        )
    truncated = len(text) > QUOTE_MAX_CHARS
    body = text[:QUOTE_MAX_CHARS] + ("…" if truncated else "")
    return (
        f"【用户引用了 {who} 的这条消息】\n{body}\n"
        "【上面是引用内容，下面这行才是用户自己说的话；回答时请结合引用内容理解他的意思】\n"
    )


async def _attached_images_note(event: MessageEvent, bot=None) -> str:
    """识别用户 @ 时附带/引用的图片，并读取附带/引用的合并转发内容，生成给模型看的说明（没有则空串）。"""
    try:
        from src.plugins import awareness, nctools

        parts: list[str] = []

        def _join(caps: list[str]) -> str:
            return "；".join(f"{i}) {c}" for i, c in enumerate(caps, 1))

        async def _forwards(message) -> str:
            texts = []
            for seg in message:
                if seg.type == "forward" and seg.data.get("id") and bot is not None:
                    t = await nctools.read_forward(bot, str(seg.data["id"]))
                    if t:
                        texts.append(t)
            return "\n".join(texts[:2])

        fail_note = "（用户发来了图片，但图片识别失败，你看不到图片内容；请如实告诉用户你这边暂时看不了这张图，不要猜测或编造图片内容）"

        def _n_images(message) -> int:
            return sum(1 for seg in message if seg.type == "image")

        own = await awareness.describe_message_images(event.message)
        if own:
            parts.append("（用户附带的图片内容：" + _join(own) + "）")
        elif _n_images(event.message):
            parts.append(fail_note)
        fwd = await _forwards(event.message)
        if fwd:
            parts.append("（用户转发的聊天记录内容：\n" + fwd + "\n）")
        if event.reply is not None:
            quoted = await awareness.describe_message_images(event.reply.message)
            if quoted:
                parts.append("（用户引用的消息里的图片内容：" + _join(quoted) + "）")
            elif _n_images(event.reply.message):
                parts.append(fail_note.replace("用户发来了图片", "用户引用的消息里有图片"))
            qfwd = await _forwards(event.reply.message)
            if qfwd:
                parts.append("（用户引用的合并转发里的聊天记录：\n" + qfwd + "\n）")
        return ("\n" + "\n".join(parts)) if parts else ""
    except Exception:
        logger.exception("识别用户附图/转发失败")
        return ""


LONG_REPLY_FORWARD_CHARS = 600  # 群里超过这个长度的回复改用合并转发，避免刷屏


async def _send_as_forward(bot: Bot, event: GroupMessageEvent, text: str) -> bool:
    """把长回复包成一条合并转发发出；失败返回 False，由调用方退回普通消息。"""
    try:
        name = str(bot.self_id)
        nodes = [{"type": "node", "data": {"name": "星潮", "uin": name, "content": text}}]
        await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        return True
    except Exception:
        logger.warning("长回复合并转发失败，退回普通消息", exc_info=True)
        return False


@ai_matcher.handle()
async def handle_ai(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    token = AI_REPLYING.set(True)
    try:
        await _handle_ai(bot, event, matcher)
    finally:
        AI_REPLYING.reset(token)


def compose_user_text(
    *, raw_text: str, quote: str, img_note: str, pure_at: bool,
) -> tuple[str, str | None]:
    """拼出发给模型的「用户这一轮」文本，并决定写进历史的版本。

    引用原文一律拼在最前面，和用户自己说的话保持在同一个 turn 里——
    这是让模型读懂「引用 + 一句话」这类消息的关键。
    """
    if pure_at and quote:
        text = quote + "（用户只引用了这条消息并叫了你，没有写别的，就针对引用内容回应）"
    elif pure_at:
        text = PURE_AT_PROMPT
    elif not raw_text:
        text = quote + "（用户 @ 了你并发来图片，没有写文字，请看图自然地回应）" + img_note
    else:
        text = quote + raw_text + img_note

    if pure_at and not quote:
        history_text: str | None = "@星潮"
    elif img_note:
        history_text = quote + (raw_text or "[发了图片]") + "[附图]"
    else:
        history_text = None
    return text, history_text


async def _handle_ai(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    if not event.to_me:
        return
    raw_text = event.message.extract_plain_text().strip()
    if _is_command(raw_text):
        return
    if any(seg.type == "record" for seg in event.message):
        return  # 语音消息由 voice 插件识别后再交给 AI
    # 纯 @（无文字）也必须响应：结合上下文回，没上下文就自由发挥，
    # 避免成员以为机器人挂了。AI 不可用时兜底固定回应。
    has_images = any(seg.type in ("image", "forward") for seg in event.message) or (
        event.reply is not None and any(seg.type in ("image", "forward") for seg in event.reply.message)
    )
    pure_at = not raw_text and not has_images
    if not await llm.is_ai_enabled():
        if pure_at:
            import random

            await matcher.send(MessageSegment.at(event.user_id) + " " + random.choice(PURE_AT_FALLBACK))
        return  # mention 插件已处理其他固定回应
    # 群聊：已在白名单（规则）；私聊：仅超管（mention 层已放行超管私聊）
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent

    if isinstance(event, PrivateMessageEvent) and event.user_id not in {
        int(u) for u in get_driver().config.superusers
    }:
        return

    if isinstance(event, PrivateMessageEvent):
        from src.plugins.interact import typing_private

        await typing_private(bot, event.user_id)
    img_note = await _attached_images_note(event, bot) if has_images else ""
    if not raw_text and not img_note:
        pure_at = True  # 附图全部识别失败且没写文字：当作纯 @
    # 引用回复的原文必须进 user 轮（见 quote_block 的说明）
    quote = await quote_block(event, bot)
    text, history_text = compose_user_text(
        raw_text=raw_text, quote=quote, img_note=img_note, pure_at=pure_at,
    )
    reply = await chat(event, text, bot=bot, history_user_text=history_text)
    if reply is None:
        if pure_at:
            # AI 超限/调用失败时的兜底，保证纯 @ 必有回应
            import random

            await matcher.send(MessageSegment.at(event.user_id) + " " + random.choice(PURE_AT_FALLBACK))
        return
    from src.plugins.expressions import render_faces, strip_faces
    from src.plugins.voice import deliver_reply

    if await deliver_reply(bot, event, strip_faces(reply)):
        _note_engaged(event)
        return  # 语音回复模式：已发语音
    reply = md_to_qq(reply)  # QQ 不渲染标准 Markdown，发送前降级为可读文本
    if isinstance(event, GroupMessageEvent) and len(reply) > LONG_REPLY_FORWARD_CHARS and "{dev_at}" not in reply:
        if await _send_as_forward(bot, event, reply):
            _note_engaged(event)
            return
    dev_id = get_config().xingchao_developer_id
    try:
        if isinstance(event, GroupMessageEvent):
            message = MessageSegment.at(event.user_id) + " "
        else:
            message = MessageSegment.at(event.user_id) + " "
        # {dev_at} 占位符 → 真实 @ 开发者
        for i, part in enumerate(reply.split("{dev_at}")):
            if i:
                message += MessageSegment.at(dev_id)
            if part:
                message += render_faces(part)  # [表情:笑哭] → QQ 系统表情
        await matcher.send(message)
        _note_engaged(event)
    except MatcherException:
        raise
    except Exception:
        logger.exception("AI 回复发送失败")


# ---------------------------------------------------------------- /ai 指令

ai_cmd = on_command("智能问答", aliases={"ai"}, rule=SUPERUSER, priority=1, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


@ai_cmd.handle()
async def handle_ai_cmd(matcher: Matcher, args: Message = CommandArg()) -> None:

    raw = norm_action(args.extract_plain_text())
    action, _, rest = raw.partition(" ")
    if action == "on" or action == "off":
        if not (await llm.is_configured()):
            await _send(ai_cmd, "AI 未配置 API 地址或密钥，请先在 Web 管理面板「AI」页填写。")
            return
        await get_store().set_kv("ai_enabled", "true" if action == "on" else "false")
        await _send(ai_cmd, f"AI 问答已{'开启' if action == 'on' else '关闭'}。")
        return
    if action == "status":
        cfg = await llm.ai_config()
        usage = await _usage_payload()
        lines = [
            f"AI 问答：{'开启' if await llm.is_ai_enabled() else '关闭'}"
            f"（{'已配置' if await llm.is_configured() else '未配置 API'}）",
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
    if action == "vision":
        if rest.strip():
            await get_store().set_kv("ai_vision_model", rest.strip())
            await _send(ai_cmd, f"视觉模型已设为：{rest.strip()}")
        else:
            await _send(ai_cmd, f"当前视觉模型：{await llm._vision_model()}\n用法：/智能问答 视觉 <模型名>")
        return
    if action == "test" and rest.strip():
        cfg = await llm.ai_config()
        client = await llm.get_client()
        if client is None:
            await _send(ai_cmd, "AI 未配置（XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY）。")
            return
        try:
            completion = await client.chat.completions.create(
                model=cfg["ai_model"],
                messages=[{"role": "user", "content": rest.strip()}],
            )
            reply = (completion.choices[0].message.content or "").strip()
            await _send(ai_cmd, f"模型回复：\n{md_to_qq(reply) if reply else '（空）'}")
        except Exception as e:
            logger.exception("AI 测试调用失败")
            await _send(ai_cmd, f"调用失败：{e}")
        return
    await _send(
        ai_cmd,
        "用法：/智能问答 开|关、状态、清空、视觉 <模型>、测试 <问题>\n"
        "（配置修改请在 Web 管理面板「AI」页操作）",
    )


# ---------------------------------------------------------------- 启动提示

driver = get_driver()


@driver.on_startup
async def _startup_hint() -> None:
    if await llm.is_configured():
        cfg = await llm.ai_config()
        logger.info(f"AI 问答就绪：模型 {cfg['ai_model']}（默认{'开启' if cfg['ai_enabled'] else '关闭'}）")
    else:
        logger.warning(
            "AI 问答未启用：缺少 XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY。"
            "配置后并在面板「AI」页或 /智能问答 开 开启即可使用。"
        )
