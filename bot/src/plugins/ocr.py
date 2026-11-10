"""图片文字识别（NapCat ocr_image）：让敏感词检测也能"看到"图片里的字。

- 本地识别，不耗模型额度；结果按图缓存，同一张图不会重复识别
- 接口不存在（如 NapCat 在该平台不支持 OCR）时自动关闭，不再反复调用，重启后重试一次；
  /能力自检 可以提前看到是否支持
- 开关 kv ocr_guard_enabled（默认开）：/plugin ocr on|off
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from nonebot.log import logger

from src.store import get_store

MAX_IMAGES = 2
MAX_CHARS = 500
_cache: "OrderedDict[str, str]" = OrderedDict()
_unsupported = False


async def ocr_enabled() -> bool:
    if _unsupported:
        return False
    return await get_store().get_kv("ocr_guard_enabled") != "false"


def parse_ocr_result(raw: Any) -> str:
    """兼容多种返回：{"texts":[{"text":..}]} / {"data":[...]} / [{"text":..}] / [str]。"""
    if isinstance(raw, dict):
        raw = raw.get("texts") or raw.get("data") or raw.get("result") or []
    parts: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        t = item.get("text") if isinstance(item, dict) else item
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return " ".join(parts)[:MAX_CHARS]


def _key(seg) -> str:
    return str(seg.data.get("file") or seg.data.get("url") or id(seg))


async def image_text(bot, seg) -> str:
    """识别单张图的文字；失败/不支持返回空串。"""
    global _unsupported
    key = _key(seg)
    if key in _cache:
        return _cache[key]
    for ref in (seg.data.get("file"), seg.data.get("url")):
        if not ref:
            continue
        try:
            text = parse_ocr_result(await bot.call_api("ocr_image", image=str(ref)))
        except Exception as e:
            from src.plugins.capabilities import UNSUPPORTED, classify_failure

            if classify_failure(e) == UNSUPPORTED:
                _unsupported = True
                logger.warning("ocr_image 不被当前 NapCat 支持，已自动关闭图片文字检测")
                return ""
            continue
        _cache[key] = text
        while len(_cache) > 200:
            _cache.popitem(last=False)
        return text
    return ""


async def images_text(bot, segs: list) -> str:
    """识别一条消息里的前几张图，拼接成一段文字。"""
    texts = [t for t in [await image_text(bot, s) for s in segs[:MAX_IMAGES]] if t]
    return "\n".join(texts)[:MAX_CHARS]
