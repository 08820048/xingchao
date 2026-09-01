"""关键词回复：精确 / 包含匹配，带冷却。词库 data/replies.json。

- 只处理纯文本消息
- 同群同词条冷却默认 8 秒，条目可覆盖
- 多条命中只回文件顺序第一条
- 命中才 block（stop_propagation），否则放行给更低优先级
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.config import get_config
from src.permission import GROUP_WHITELIST
from src.store import get_store

_DEFAULT_REPLY_FILE: dict[str, Any] = {
    "version": 1,
    "items": [
        {
            "id": "welcome",
            "enabled": True,
            "match": "exact",
            "pattern": "你好星潮",
            "reply": "在。发送 /help 查看指令。",
            "cooldown": 8,
        }
    ],
}

_items: list[dict[str, Any]] = []
_loaded: bool = False
_enabled: bool = True
_last_hit: dict[tuple[int, str], float] = {}

reply_matcher = on_message(rule=GROUP_WHITELIST, priority=20, block=False)


def _ensure_file() -> None:
    path = get_config().xingchao_replies_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_DEFAULT_REPLY_FILE, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info(f"词库文件不存在，已写入默认示例：{path}")


def load_replies() -> None:
    """（重）加载词库；解析失败保留旧词库。"""
    global _items, _loaded
    path = get_config().xingchao_replies_path
    try:
        _ensure_file()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception(f"读取词库失败，保留当前词库：{path}")
        return
    items: list[dict[str, Any]] = []
    for raw in data.get("items", []):
        if not isinstance(raw, dict):
            continue
        if not raw.get("id") or not isinstance(raw.get("pattern"), str) or not isinstance(
            raw.get("reply"), str
        ):
            logger.warning(f"跳过无效词条：{raw!r}")
            continue
        if raw.get("match", "exact") not in ("exact", "contains"):
            logger.warning(f"词条 match 字段无效（只支持 exact/contains）：{raw.get('id')}")
            continue
        items.append(raw)
    _items = items
    _loaded = True
    logger.info(f"词库已加载：{len(items)} 条词条（{path}）")


def get_items() -> list[dict[str, Any]]:
    if not _loaded:
        load_replies()
    return _items


def reload_replies() -> int:
    load_replies()
    return len(_items)


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = value


async def _restore_enabled_from_kv() -> None:
    value: Optional[str] = await get_store().get_kv("reply_enabled")
    if value is not None:
        set_enabled(value == "true")


driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    try:
        await _restore_enabled_from_kv()
    except Exception:
        logger.exception("恢复关键词模块开关状态失败，默认开启")


def _command_start() -> set[str]:
    return {s for s in get_driver().config.command_start if s}


@reply_matcher.handle()
async def handle_reply(event: GroupMessageEvent, matcher: Matcher) -> None:
    if not _enabled:
        return
    if not event.message or not all(seg.type == "text" for seg in event.message):
        return  # 只对纯文本
    text = event.message.extract_plain_text().strip()
    if not text:
        return
    if any(text.startswith(start) for start in _command_start()):
        return  # 指令交给指令 matcher

    cfg = get_config()
    now = time.monotonic()
    for item in get_items():
        if not item.get("enabled", True):
            continue
        pattern = str(item["pattern"])
        if str(item.get("match", "exact")) == "exact":
            hit = text == pattern
        else:
            hit = pattern in text
        if not hit:
            continue

        item_id = str(item["id"])
        cooldown = float(item.get("cooldown", cfg.xingchao_reply_cooldown))
        key = (event.group_id, item_id)
        last = _last_hit.get(key)
        if last is not None and now - last < cooldown:
            logger.debug(f"词条 {item_id} 在群 {event.group_id} 冷却期内，跳过")
            return
        _last_hit[key] = now
        try:
            await matcher.send(str(item["reply"]))
        except MatcherException:
            raise
        except Exception:
            logger.exception(f"关键词回复发送失败：词条 {item_id} 群 {event.group_id}")
            return
        matcher.stop_propagation()  # 命中才 block
        return
