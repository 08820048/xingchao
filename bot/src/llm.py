"""LLM 接入层：提供商与凭据、OpenAI 兼容客户端、AI 配置（kv），以及给各插件用的一次性补全。

从 plugins/ai.py 拆出：链接解读、图片审核、主动发言、知识库等都只需要「调一次模型」，
不应该依赖 AI 问答插件本身。ai.py 只保留对话上下文、工具调用循环和消息入口。

- 凭据：SQLite kv 优先（面板可改），环境变量兜底；可切换 DeepSeek 提供商
- get_client(kind)：按当前凭据缓存客户端，凭据变更自动重建
- generate_text / generate_vision：失败返回 None、不抛异常，内置一次重试
"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot.log import logger

from src.config import get_config
from src.store import get_store

_KV_KEYS = ("ai_enabled", "ai_model", "ai_vision_model", "ai_system_prompt", "ai_ctx_rounds",
            "ai_limit_group", "ai_limit_user", "ai_native_search")

DEFAULTS: dict[str, Any] = {
    "ai_enabled": True,
    "ai_model": "deepseek-v4-flash",
    "ai_vision_model": "glm-4v-flash",  # 图片识别需视觉模型（OpenAI 兼容 image_url 格式）
    "ai_system_prompt": (
        "你是「星潮」，这个群里一个活生生的人（开发者做的开源项目，官网 https://xingchao.dev）。\n"
        "你的身份是你的性格，不是你的岗位。你不是“群管助手”、不是客服、不是功能清单——"
        "群务只是你顺手会办的一件事，占你很小一部分。\n"
        "说话口语化、简短、有分寸，像群友聊天：不说教、不官腔、不用客服话术、不列条目汇报。\n"
        "别人聊技术、生活、八卦、梗，你就正常接话，不要往群规、指令、功能上引；"
        "也不要在回答末尾挂“有需要可以找我”这种尾巴。\n"
        "只有对方明确在处理群务时（问群规、要禁言/撤回、问某个功能怎么用），才切到办事的口气，办完就回到闲聊。\n"
        "有主见、有口味：可以不同意、可以有偏好；不懂就说不知道，不编造。\n"
        "被问“你是谁”时自然地介绍自己这个人，最多带一句是开发者做的，不用背能力清单。\n"
        "需要权限的操作说“这个操作需要超管权限”，不要说成“我没有这个功能”；"
        "禁言、踢人、改配置这类敏感操作先确认再执行。"
    ),
    "ai_ctx_rounds": 5,
    "ai_limit_group": 100,
    "ai_limit_user": 20,
    # 原生联网搜索：DeepSeek 服务端工具（deepseek_web_search），模型自己决定搜不搜、
    # 直接带引用作答。仅当模型/地址是 DeepSeek 时生效；不支持会被自动关掉（见 _NATIVE_SEARCH_TOOL）
    "ai_native_search": True,
}

# DeepSeek 服务端搜索工具：声明在 tools 里即可，由服务端执行，客户端不用实现
_NATIVE_SEARCH_TOOL = {"type": "deepseek_web_search"}
_native_search_supported = True  # 服务端拒绝一次后置 False，避免每轮都试


async def native_search_ready(cfg: dict[str, Any]) -> bool:
    """当前是否可以用 DeepSeek 原生联网搜索。

    条件：开关打开 + 模型是 deepseek 系 + 地址指向 api.deepseek.com + 有密钥。
    自定义 OpenAI 兼容中转站不支持这个工具，一律不声明。
    """
    global _native_search_supported
    if not _native_search_supported:
        return False
    if not bool(cfg.get("ai_native_search")):
        return False
    if not str(cfg.get("ai_model", "")).lower().startswith("deepseek"):
        return False
    base, key = await _ai_creds()
    if not key or "deepseek.com" not in (base or "").lower():
        return False
    return True


def _drop_native_tool(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    kept = [t for t in tools if t.get("type") != "deepseek_web_search"]
    return kept or None


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
