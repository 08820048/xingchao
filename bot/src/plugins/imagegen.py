"""AI 绘图：/画 <描述>，或对 AI 说"画一只在太空的猫"。

- 走默认 AI 提供商的 OpenAI 兼容接口 images.generate；模型由 kv image_model 指定，**留空即不启用**
  （提供商是否有绘图模型、模型叫什么因服务商而异，所以不预设）。/画 在未配置时会告诉用户。
- 护栏：提示词先过敏感词库（命中直接拒绝）；每人每天 3 张、全局每天 30 张、同一人 30 秒冷却；
  生成可能耗时较长，超时 90 秒
- 返回 url 就直接发 url，返回 b64_json 就以 base64 发送
- AI 工具：generate_image（所有人）
"""

from __future__ import annotations

import time
from datetime import datetime

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import BASIC
from src.store import get_store

PER_USER_DAILY = 3
GLOBAL_DAILY = 30
COOLDOWN = 30.0
PROMPT_MAX = 300
TIMEOUT = 90

_user_daily: dict[tuple[int, str], int] = {}
_global_daily: tuple[str, int] = ("", 0)
_last: dict[int, float] = {}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def check_quota(user_id: int, now: float | None = None) -> str | None:
    """超限返回提示，否则 None（不扣额度）。"""
    now = time.monotonic() if now is None else now
    last = _last.get(user_id)
    if last is not None and now - last < COOLDOWN:
        return "画得有点快，稍等几十秒再来～"
    day = _today()
    if _user_daily.get((user_id, day), 0) >= PER_USER_DAILY:
        return f"你今天已经画了 {PER_USER_DAILY} 张啦，明天再来～"
    gd, gn = _global_daily
    if gd == day and gn >= GLOBAL_DAILY:
        return "今天的绘图额度用完啦，明天再来～"
    return None


def _consume(user_id: int, now: float | None = None) -> None:
    global _global_daily
    day = _today()
    _user_daily[(user_id, day)] = _user_daily.get((user_id, day), 0) + 1
    for k in [k for k in _user_daily if k[1] != day]:
        _user_daily.pop(k, None)
    gd, gn = _global_daily
    _global_daily = (day, (gn if gd == day else 0) + 1)
    _last[user_id] = time.monotonic() if now is None else now


async def _prompt_blocked(prompt: str) -> bool:
    try:
        from src.plugins import sensitive

        words = await sensitive.get_words()
        return bool(words) and sensitive.find_hit(prompt, words, [], "normal") is not None
    except Exception:
        logger.debug("绘图提示词敏感词检查失败，放行交给提供商审核", exc_info=True)
        return False


async def generate(user_id: int, prompt: str) -> tuple[MessageSegment | None, str]:
    """返回 (图片消息段, 提示)；失败时消息段为 None、提示是原因。"""
    prompt = " ".join(prompt.split())[:PROMPT_MAX]
    if not prompt:
        return None, "想画什么？例如：/画 一只戴墨镜的柴犬"
    model = (await get_store().get_kv("image_model") or "").strip()
    if not model:
        return None, "绘图功能还没启用（管理员需要先配置绘图模型 kv image_model）。"
    if (tip := check_quota(user_id)) is not None:
        return None, tip
    if await _prompt_blocked(prompt):
        return None, "这个主题我不画哦。"
    from src.plugins import ai

    client = await ai.get_client()
    if client is None:
        return None, "AI 还没配置好，暂时不能画图。"
    try:
        res = await client.images.generate(model=model, prompt=prompt, n=1, size="1024x1024", timeout=TIMEOUT)
        item = res.data[0]
        if getattr(item, "url", None):
            seg = MessageSegment.image(item.url)
        elif getattr(item, "b64_json", None):
            seg = MessageSegment.image("base64://" + item.b64_json)
        else:
            return None, "绘图服务没有返回图片，稍后再试。"
    except Exception as e:
        logger.warning(f"绘图失败：{type(e).__name__}: {e}")
        return None, "画图失败了（服务不可用或模型不支持绘图），稍后再试。"
    _consume(user_id)
    return seg, ""


draw_cmd = on_command("画", aliases={"draw", "绘图"}, rule=BASIC, priority=5, block=True)


@draw_cmd.handle()
async def handle_draw(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    prompt = args.extract_plain_text().strip()
    if prompt:
        await matcher.send("收到，画图中，可能要一会儿…")
    seg, tip = await generate(event.user_id, prompt)
    await matcher.send(seg if seg is not None else tip)
