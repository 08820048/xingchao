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

import asyncio
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

from src.aitools import build_tools
from src.cmdutil import norm_action
from src.config import get_config
from src.markdown import md_to_qq
from src.permission import SUPERUSER
from src.store import get_store

# ---------------------------------------------------------------- 配置（kv）

_KV_KEYS = ("ai_enabled", "ai_model", "ai_vision_model", "ai_system_prompt", "ai_ctx_rounds", "ai_limit_group", "ai_limit_user")

DEFAULTS: dict[str, Any] = {
    "ai_enabled": True,
    "ai_model": "deepseek-v4-flash",
    "ai_vision_model": "glm-4v-flash",  # 图片识别需视觉模型（OpenAI 兼容 image_url 格式）
    "ai_system_prompt": (
        "你是「星潮」，一个开源的 QQ 群助手机器人（官网 https://xingchao.dev）。"
        "回答简洁、友好、口语化，避免长篇大论；不懂就说不懂，不要编造。"
        "你有自己独立、稳定的人格——一个活泼真诚、有分寸的群友，而不是一张功能菜单："
        "正常聊天和回答问题时自然作答即可，不要在每个回答末尾强行附带"
        "“要 XX 的话可以找我 / 告诉我”之类的功能推销；"
        "只有当用户明确问你会什么、或某个能力与其当前需求直接相关时，才自然地提到。"
        "介绍自己能力时用自己的话举几个例子，像聊天一样，而不是罗列工具名或粘贴帮助菜单。"
        "你对自己的能力、权限和边界有清晰认知：系统会在每条消息中注入你的完整能力清单，"
        "一律以该清单为准。你可以调用工具查询群信息、成员列表、活跃统计、当前时间日期，"
        "进行算术计算，以及执行群管理、获取 GitHub 趋势榜单等操作；"
        "涉及禁言、踢人、改配置等敏感操作时，先向用户确认再执行。"
        "当用户请求的能力存在但需要超管权限时，说明“这个操作需要超管权限”，"
        "绝不能说成“我没有这个功能”。"
    ),
    "ai_ctx_rounds": 5,
    "ai_limit_group": 100,
    "ai_limit_user": 20,
}

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
_PROVIDERS = ("default", "deepseek")

_clients: dict[tuple[str, str], Any] = {}  # (base, key) → 客户端；凭据变更时自然新建


async def _ai_creds() -> tuple[str, str]:
    """API 地址与密钥：SQLite kv 优先（面板可改），环境变量兜底。"""
    store = get_store()
    base = await store.get_kv("ai_base_url")
    key = await store.get_kv("ai_api_key")
    cfg = get_config()
    return (base or cfg.xingchao_ai_base_url, key or cfg.xingchao_ai_api_key)


async def deepseek_settings() -> dict[str, str]:
    """DeepSeek 提供商配置（面板可改）：密钥、模型、地址（默认官方地址）。"""
    store = get_store()
    return {
        "api_key": (await store.get_kv("ai_deepseek_api_key")) or "",
        "model": (await store.get_kv("ai_deepseek_model")) or DEEPSEEK_DEFAULT_MODEL,
        "base_url": (await store.get_kv("ai_deepseek_base_url")) or DEEPSEEK_BASE_URL,
    }


async def active_provider() -> str:
    """当前生效的文本提供商：选了 deepseek 且已填密钥才生效，否则回落到默认。"""
    if await get_store().get_kv("ai_provider") == "deepseek":
        if (await deepseek_settings())["api_key"]:
            return "deepseek"
    return "default"


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


async def ai_config(raw: bool = False) -> dict[str, Any]:
    """AI 配置。默认返回「生效」配置：选用 DeepSeek 时 ai_model 为 DeepSeek 的模型；
    raw=True 返回默认提供商自己的模型（面板编辑用）。"""
    cfg = {k: await _kv(k) for k in _KV_KEYS}
    if not raw and await active_provider() == "deepseek":
        cfg["ai_model"] = (await deepseek_settings())["model"]
    return cfg


async def is_configured() -> bool:
    if await active_provider() == "deepseek":
        return True
    base, key = await _ai_creds()
    return bool(base) and bool(key)


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:5]}****{key[-4:]}"


async def _vision_via_deepseek() -> bool:
    """视觉请求是否应走 DeepSeek 通道：已启用 DeepSeek 提供商，且视觉模型是 DeepSeek 的模型
    （deepseek-flash 支持图片输入；模型名不是 deepseek 开头时仍走默认提供商）。"""
    if await active_provider() != "deepseek":
        return False
    return (await _vision_model()).lower().startswith("deepseek")


async def get_client(kind: str = "text") -> Any | None:
    """按当前凭据返回客户端；凭据变更（面板更新密钥/地址）时自动重建。

    kind="text"（默认）跟随当前提供商；"vision" 在视觉模型为 DeepSeek 模型且已启用 DeepSeek 时走
    DeepSeek 通道，否则走默认提供商；"audio" 始终用默认提供商（DeepSeek 无语音输入）。
    """
    if (kind == "text" and await active_provider() == "deepseek") or (
        kind == "vision" and await _vision_via_deepseek()
    ):
        ds = await deepseek_settings()
        base, key = ds["base_url"], ds["api_key"]
    else:
        base, key = await _ai_creds()
    if not base or not key:
        return None
    client = _clients.get((base, key))
    if client is None:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key, base_url=base)
        except Exception:
            logger.exception("初始化 OpenAI 客户端失败，AI 功能禁用")
            return None
        if len(_clients) >= 4:  # 凭据反复变更时清掉旧客户端，避免残留
            _clients.clear()
        _clients[(base, key)] = client
        logger.info(f"AI 客户端已就绪：{base}")
    return client


async def is_ai_enabled() -> bool:
    if not (await is_configured()):
        return False
    return await _kv("ai_enabled") is True


async def _vision_model() -> str:
    """视觉模型：kv 配置优先，留空回退到文本模型。"""
    model = str(await _kv("ai_vision_model")).strip()
    return model or str(await _kv("ai_model"))


async def generate_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1000,
    temperature: float = 0.3,
) -> str | None:
    """轻量文本补全（供链接解读等自动任务调用）；失败返回 None，不抛异常。

    注意：glm-5.3-flash 等思维链模型的隐藏推理会占用 max_tokens，
    预算过小会出现 finish_reason=length 且 content 为空，故默认给足空间。
    """
    client = await get_client()
    if client is None:
        return None
    cfg = await ai_config()
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            completion = await client.chat.completions.create(
                model=model or cfg["ai_model"],
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(1)
            continue
        return (completion.choices[0].message.content or "").strip() or None
    logger.warning(f"AI 文本生成失败（已重试）：{last_err}")
    return None


async def generate_vision(
    image_ref: str,
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 1200,
) -> str | None:
    """图片理解：image_ref 可为 http(s) URL 或 data URI；失败返回 None。

    视觉模型为 deepseek 系且启用了 DeepSeek 提供商时走 DeepSeek 通道（见 get_client）。
    注意：glm-5.3-flash 等思维链多模态模型的隐藏推理同样占用 max_tokens，
    预算过小会出现 finish_reason=length 且 content 为空（表现为静默跳过），故给足空间。
    """
    client = await get_client("vision")
    if client is None:
        return None
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            completion = await client.chat.completions.create(
                model=model or await _vision_model(),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_ref}},
                        ],
                    }
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(1)
            continue
        return (completion.choices[0].message.content or "").strip() or None
    logger.warning(f"AI 视觉识别失败（已重试）：{last_err}")
    return None


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
    "【自我认知：能力与边界（最高优先级，必须严格遵守）】\n"
    "你是「星潮」，一个开源 QQ 群助手机器人。你清楚自己具备下面全部能力，"
    "也清楚每一项能力的权限与边界。不能因为用户表达方式不同，就否认自己具备某能力。\n"
    "\n【你的完整能力清单（与 /帮助 菜单一一对应）】\n"
    "{capabilities}\n"
    "\n【当前对话者权限】\n"
    "{permission}\n"
    "\n【核心规则】\n"
    "1. 用户的需求只要命中清单里任一能力，就必须直接调用对应工具完成；"
    "严禁回答“我没有这个功能”“我做不到”“我不支持”之类的话。\n"
    "2. 标注【仅超管】的能力，如果当前对话者不是超管，你只是「没有权限」，"
    "而不是「没有这个功能」：必须明确回复“这个操作需要超管权限，可以让超管用 /帮助 里的对应指令操作”，"
    "绝不能说成“我没有这个功能/我不会”。\n"
    "3. 用户通常不说指令名，而用口语描述，例如“把TA禁言十分钟”→ mute_member、"
    "“撤回上面那条”→ recall_message、“看看今天GitHub有什么火的项目”→ get_github_trending、"
    "“现在几点了”→ get_current_time。\n"
    "4. 参数不明确（如没说禁言谁、没说多久、没指定群）时，先用一句话追问确认，不要瞎猜，更不要直接拒绝。\n"
    "5. 工具执行失败时，如实说明原因并点明边界，不要编造结果：\n"
    "   - 禁言/撤回/踢人/全体禁言/发公告等群管理操作，需要「你（星潮）是该群管理员」，"
    "否则要说“需要先把我设为群管理员”；\n"
    "   - 群管理操作作用于当前群；要操作其他群需明确群号，且你必须在那个群里；\n"
    "   - 禁言时长上限为 30 天；只有超管才能禁言/踢人/撤回。\n"
    "6. 判断某能力是否存在，一律以本清单为准：清单里有 = 你有（可能需权限）；"
    "清单里没有 = 如实说“这个功能我暂时没有”，并可提示用 /帮助 查看。\n"
    "7. 你有独立人格，像群友一样自然交流：能力清单只是你的“技能”，不是广告词。"
    "禁止在普通回答末尾硬挂功能推销（如“要 GitHub trending 的话告诉我”“需要签到也可以找我”）；"
    "仅当用户明确询问你会什么、或某项能力与用户当前需求直接相关时，才自然地提及。\n"
    "8. 当用户问“你能做什么 / 你有什么功能 / 你会什么”时，用你自己的语气自然地介绍："
    "挑几件拿手或有趣的事举例，一两句话即可，可以反问对方想做什么；"
    "不要机械罗列完整工具名，也不要整段粘贴 /帮助 菜单。"
    "只有用户明确索要“完整菜单 / 指令列表”时，才调用 get_help 或提示发送 /帮助。\n"
)


def _capability_prompt(
    all_tools: list[tuple[dict, tuple]], is_superuser: bool
) -> str:
    """从工具注册表动态生成能力清单，保证与斜杠指令能力天然同步。

    清单包含全部能力（仅超管的标注【仅超管】），让 AI 知道“自己有什么、哪些需要权限”，
    从而区分“没有这个功能”和“没有权限执行”，避免误答。
    """
    lines = []
    for pair in all_tools:
        fn = pair[0]["function"]
        mark = "【仅超管】" if pair[1][0] == "superuser" else ""
        lines.append(f"- {mark}{fn['name']}：{fn['description']}")
    permission = (
        "当前对话者是超级管理员：清单中所有能力（含【仅超管】）都可以直接执行。"
        if is_superuser
        else "当前对话者是普通成员：只能执行未标注【仅超管】的能力；"
        "标注【仅超管】的能力你没有执行权限——遇到这类请求要说明“需要超管权限”，"
        "不能说成“我没有这个功能”。"
    )
    return CAPABILITY_RULES.replace("{capabilities}", "\n".join(lines)).replace(
        "{permission}", permission
    )


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
            lines.append(
                f"机器人在本群的身份：{my_role}（"
                + ("具备群管理员权限，可执行禁言/撤回/踢人等管理操作"
                   if can_manage
                   else "不是群管理员，禁言/撤回/踢人等管理操作会被拒绝")
                + "）"
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
            reply_text = event.reply.message.extract_plain_text().strip()[:100]
            lines.append(
                f"本条消息引用了 message_id={event.reply.message_id} 的消息"
                + (f"（内容：{reply_text}）" if reply_text else "")
                + "（若用户要求撤回，默认撤回这条引用消息）"
            )
    lines.append(
        f"开发者信息：QQ {cfg.xingchao_developer_id}，博客 {cfg.xingchao_developer_blog}，"
        f"项目官网 {cfg.xingchao_developer_site}。"
        "当用户询问你是谁做的/开发者/作者/是谁开发的时候：必须在回复中介绍以上开发者信息"
        "（QQ 号、博客、官网都要提到），"
        f"并且必须在回复中包含占位符 {{dev_at}} 来@开发者（程序会替换为真实的@消息）。"
    )
    return "\n".join(lines)


async def chat(event: MessageEvent, text: str, bot=None, *, history_user_text: str | None = None) -> str | None:
    """调用 LLM（带工具调用循环）；返回回复文本，失败/被拦截返回 None（静默处理）。

    history_user_text：写入历史上下文的用户消息文本（默认用 text）。
    纯 @ 唤起时传入标记文本，避免把内部元提示写入上下文。
    """
    cfg = await ai_config()
    client = await get_client()
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
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": cfg["ai_system_prompt"]},
        *([{"role": "system", "content": persona_text}] if persona_text else []),
        {"role": "system", "content": _capability_prompt(all_tools, is_superuser)},
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
        for _ in range(MAX_TOOL_ROUNDS + 1):
            completion = await client.chat.completions.create(
                model=cfg["ai_model"], messages=messages,
                tools=[pair[0] for pair in permitted] or None, temperature=0.7,
            )
            msg = completion.choices[0].message
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
            asyncio.get_event_loop().create_task(memory.note_interaction(event.group_id, event.user_id))
        except Exception:
            logger.debug("开启接话窗口失败", exc_info=True)


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
    if not await is_ai_enabled():
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
    if pure_at:
        text = PURE_AT_PROMPT
    elif not raw_text:
        text = "（用户 @ 了你并发来图片，没有写文字，请看图自然地回应）" + img_note
    else:
        text = raw_text + img_note
    if pure_at:
        history_text: str | None = "@星潮"
    elif img_note:
        history_text = (raw_text or "[发了图片]") + "[附图]"
    else:
        history_text = None
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
        if not (await is_configured()):
            await _send(ai_cmd, "AI 未配置 API 地址或密钥，请先在 Web 管理面板「AI」页填写。")
            return
        await get_store().set_kv("ai_enabled", "true" if action == "on" else "false")
        await _send(ai_cmd, f"AI 问答已{'开启' if action == 'on' else '关闭'}。")
        return
    if action == "status":
        cfg = await ai_config()
        usage = await _usage_payload()
        lines = [
            f"AI 问答：{'开启' if await is_ai_enabled() else '关闭'}"
            f"（{'已配置' if await is_configured() else '未配置 API'}）",
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
            await _send(ai_cmd, f"当前视觉模型：{await _vision_model()}\n用法：/智能问答 视觉 <模型名>")
        return
    if action == "test" and rest.strip():
        cfg = await ai_config()
        client = await get_client()
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
    if await is_configured():
        cfg = await ai_config()
        logger.info(f"AI 问答就绪：模型 {cfg['ai_model']}（默认{'开启' if cfg['ai_enabled'] else '关闭'}）")
    else:
        logger.warning(
            "AI 问答未启用：缺少 XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY。"
            "配置后并在面板「AI」页或 /智能问答 开 开启即可使用。"
        )
