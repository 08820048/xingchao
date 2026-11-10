"""NapCat 扩展接口的薄封装（只做调用 + 容错解析，供各功能复用）。

- at_all_allowed：@全体前检查剩余次数 / 是否有权限
- translate_zh：NapCat 自带英译中（免费，不耗模型额度）
- read_forward：读取合并转发里的聊天内容
- check_url：QQ 安全中心的链接安全检查（带缓存）
各接口的返回结构在不同 NapCat 版本里可能略有差异，这里一律宽松解析，解析不了就当"未知"，不抛异常。
"""

from __future__ import annotations

import time
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

# ---------------------------------------------------------------- @全体

async def at_all_allowed(bot, group_id: int) -> tuple[bool, str]:
    """返回 (能否 @全体, 不能的原因)。接口失败视为"未知，照常尝试"。"""
    try:
        r = await bot.call_api("get_group_at_all_remain", group_id=group_id)
    except Exception:
        return True, ""
    if not isinstance(r, dict):
        return True, ""
    if r.get("can_at_all") is False:
        return False, "机器人当前不能 @全体（可能不是管理员）"
    if r.get("remain_at_all_count_for_group") == 0:
        return False, "本群今日 @全体 次数已用完"
    if r.get("remain_at_all_count_for_uin") == 0:
        return False, "机器人今日 @全体 次数已用完"
    return True, ""


# ---------------------------------------------------------------- 翻译

def parse_translation(raw: Any, expected: int) -> list[str] | None:
    if isinstance(raw, dict):
        raw = raw.get("words") or raw.get("data") or raw.get("result")
    if isinstance(raw, list) and len(raw) == expected and all(isinstance(x, str) for x in raw):
        return [x.strip() for x in raw]
    return None


async def translate_zh(bot, texts: list[str]) -> list[str] | None:
    """英译中；失败/条数对不上返回 None，调用方保留原文。"""
    if not texts:
        return []
    try:
        return parse_translation(await bot.call_api("translate_en2zh", words=texts), len(texts))
    except Exception:
        logger.debug("NapCat 翻译失败", exc_info=True)
        return None


# ---------------------------------------------------------------- 合并转发

def forward_to_text(raw: Any, max_msgs: int = 15, max_chars: int = 800) -> str:
    from src.plugins.awareness import summarize_message

    rows = raw.get("messages") if isinstance(raw, dict) else raw
    lines: list[str] = []
    for m in (rows if isinstance(rows, list) else [])[:max_msgs]:
        if not isinstance(m, dict):
            continue
        sender = m.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or m.get("nickname") or "?"
        content = m.get("content", m.get("message"))
        if isinstance(content, str):
            message = Message(content)
        else:
            message = Message([MessageSegment(s["type"], s.get("data") or {})
                               for s in (content or []) if isinstance(s, dict) and s.get("type")])
        text, images, extras = summarize_message(message)
        bits = [text.replace("\n", " ")[:120]] if text else []
        bits += ["[图片]"] * len(images) + extras
        if bits:
            lines.append(f"{name}: {' '.join(bits)}")
    out = "\n".join(lines)
    return out[:max_chars] + ("…" if len(out) > max_chars else "")


async def read_forward(bot, forward_id: str) -> str:
    """读取一条合并转发的内容（文字化）；读不到返回空串。"""
    for params in ({"message_id": forward_id}, {"id": forward_id}):
        try:
            text = forward_to_text(await bot.call_api("get_forward_msg", **params))
        except Exception:
            continue
        if text:
            return text
    return ""


# ---------------------------------------------------------------- 链接安全

_URL_CACHE_TTL = 6 * 3600
_url_cache: dict[str, tuple[float, str]] = {}
_url_unsupported = False


def url_level(raw: Any) -> str:
    """QQ 安全中心结果 → safe / unknown / danger（level: 1 安全，2 未知，3 危险）。"""
    level = raw.get("level") if isinstance(raw, dict) else raw
    if str(level) == "1":
        return "safe"
    if str(level) == "3":
        return "danger"
    return "unknown"


async def check_url(bot, url: str, now: float | None = None) -> str:
    """查询链接安全等级（6 小时缓存）；接口不存在时自动停用，之后一律返回 unknown。"""
    global _url_unsupported
    if _url_unsupported:
        return "unknown"
    now = now if now is not None else time.time()
    hit = _url_cache.get(url)
    if hit and now - hit[0] < _URL_CACHE_TTL:
        return hit[1]
    try:
        level = url_level(await bot.call_api("check_url_safely", url=url))
    except Exception as e:
        from src.plugins.capabilities import UNSUPPORTED, classify_failure

        if classify_failure(e) == UNSUPPORTED:
            _url_unsupported = True
            logger.warning("check_url_safely 不被当前 NapCat 支持，已停用链接安全检查")
        return "unknown"
    _url_cache[url] = (now, level)
    while len(_url_cache) > 500:
        _url_cache.pop(next(iter(_url_cache)))
    return level


def known_dangerous(url: str, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    hit = _url_cache.get(url)
    return bool(hit and hit[1] == "danger" and now - hit[0] < _URL_CACHE_TTL)
