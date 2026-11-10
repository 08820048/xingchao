"""收藏表情包库：读取机器人账号 QQ 里收藏的表情包，让它能在聊天里按心情发表情包。

- 来源：NapCat fetch_custom_face（机器人账号手动收藏的表情；库里有什么取决于你收藏了什么）
- 语义标签：每张新表情包让视觉模型打一次 2–6 字标签（如“无语/开心/狗头”），结果存 kv，
  不重复花钱；每次刷新最多标 6 张、每天最多标 40 张
- 使用：主动发言的决策里多一个 sticker 动作（按编号挑）；AI 工具 send_sticker(mood) 按心情发一张
- 刷新：懒加载，距上次超过 6 小时才重新拉取列表
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

from src.store import get_store

REFRESH_SECONDS = 6 * 3600
TAGS_PER_REFRESH = 6
TAGS_PER_DAY = 40
CATALOG_SIZE = 20
TAG_PROMPT = (
    "用 2 到 6 个字概括这张表情包表达的情绪或梗（如：无语、开心、狗头、委屈、吃瓜、加油）；"
    "如果图上有文字，用文字含义。只输出标签，不要标点和解释。"
)

_urls: dict[str, str] = {}      # sticker_id → 当前 url
_last_refresh = 0.0
_tag_day: tuple[str, int] = ("", 0)


def sticker_id(url: str) -> str:
    """用不含查询参数的 url 做稳定 ID（CDN 的签名参数会变）。"""
    return hashlib.md5(url.split("?")[0].encode()).hexdigest()[:12]


def parse_face_list(raw: Any) -> list[str]:
    """fetch_custom_face 的返回在不同版本里可能是 url 列表或 {url:...} 列表。"""
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("list") or raw.get("urls") or []
    urls: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        u = item if isinstance(item, str) else (item or {}).get("url") if isinstance(item, dict) else None
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
    return urls


async def _tags() -> dict[str, str]:
    raw = await get_store().get_kv("sticker_tags")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _save_tags(tags: dict[str, str]) -> None:
    if len(tags) > 300:  # 只留最近的 300 条
        tags = dict(list(tags.items())[-300:])
    await get_store().set_kv("sticker_tags", json.dumps(tags, ensure_ascii=False))


def _clean_tag(raw: str | None) -> str | None:
    tag = re.sub(r"[\s，。,.！!？?「」\"'`]", "", raw or "")[:8]
    return tag or None


async def _tag_one(url: str) -> str | None:
    from src.plugins import ai as ai_plugin
    from src.plugins.visionguard import _download_as_data_uri

    ref = await _download_as_data_uri(url) or url
    return _clean_tag(await ai_plugin.generate_vision(ref, TAG_PROMPT, max_tokens=300))


async def ensure_library(bot, *, force: bool = False) -> int:
    """刷新表情包列表并给新表情打标签；返回当前可用（有标签）的表情包数。"""
    global _last_refresh, _tag_day
    now = time.time()
    if not force and _urls and now - _last_refresh < REFRESH_SECONDS:
        return len([i for i in _urls if i in await _tags()])
    try:
        urls = parse_face_list(await bot.call_api("fetch_custom_face", count=48))
    except Exception:
        logger.debug("获取收藏表情失败", exc_info=True)
        return 0
    _urls.clear()
    _urls.update({sticker_id(u): u for u in urls})
    _last_refresh = now
    tags = await _tags()
    day = datetime.now().strftime("%Y-%m-%d")
    used = _tag_day[1] if _tag_day[0] == day else 0
    todo = [i for i in _urls if i not in tags][: min(TAGS_PER_REFRESH, max(0, TAGS_PER_DAY - used))]
    changed = False
    for sid in todo:
        try:
            tag = await _tag_one(_urls[sid])
        except Exception:
            logger.debug("表情包打标签失败", exc_info=True)
            continue
        used += 1
        if tag:
            tags[sid] = tag
            changed = True
    _tag_day = (day, used)
    if changed:
        await _save_tags(tags)
    return len([i for i in _urls if i in tags])


async def catalog() -> tuple[str, list[str]]:
    """给决策提示词用：随机挑最多 20 张有标签的，返回 (“1.无语 2.开心…”, 编号→sticker_id)。"""
    tags = await _tags()
    avail = [i for i in _urls if i in tags]
    random.shuffle(avail)
    pick = avail[:CATALOG_SIZE]
    return " ".join(f"{n}.{tags[i]}" for n, i in enumerate(pick, 1)), pick


async def pick_by_mood(mood: str) -> str | None:
    """按心情关键词挑一张；没有匹配就随机一张；库为空返回 None。"""
    tags = await _tags()
    avail = [i for i in _urls if i in tags]
    if not avail:
        return None
    mood = mood.strip()
    hits = [i for i in avail if mood and (mood in tags[i] or tags[i] in mood)]
    return random.choice(hits or avail)


def sticker_message(sid: str) -> Message | None:
    url = _urls.get(sid)
    if not url:
        return None
    return Message([MessageSegment("image", {"file": url, "sub_type": 1, "summary": "[动画表情]"})])
