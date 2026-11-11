"""面板公共部分：认证、群名缓存、状态数据等（各路由模块共享）。"""


import hashlib
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot import get_loaded_plugins
from nonebot.log import logger

from src.config import get_config
from src.permission import (
    disabled_groups,
    merged_whitelist,
)
from src.store import get_store

_STARTED_AT = time.monotonic()
_COOKIE_NAME = "xingchao_panel"
_password_sha256: str = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _init_password() -> None:
    global _password_sha256
    cfg = get_config()
    if cfg.xingchao_panel_password:
        _password_sha256 = _sha256(cfg.xingchao_panel_password)
        logger.info("Web 面板密码来自 XINGCHAO_PANEL_PASSWORD")
    else:
        raw = secrets.token_urlsafe(12)
        _password_sha256 = _sha256(raw)
        logger.warning(
            f"未设置 XINGCHAO_PANEL_PASSWORD，已随机生成面板密码：{raw} "
            "（仅本次启动有效，建议写入 .env）"
        )


def _authorized(request: Request) -> bool:
    return request.cookies.get(_COOKIE_NAME, "") == _password_sha256


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "未登录或会话失效"}, status_code=401)


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_day(day: str) -> bool:
    return bool(_DAY_RE.match(day))


def _panel_bot():
    """取当前连接的机器人实例（面板操作需要主动调用 API 时用）。"""
    from nonebot import get_bots

    return next(iter(get_bots().values()), None)


_GROUP_NAMES: dict = {"ts": 0.0, "map": {}}
_GROUP_NAMES_TTL = 300


async def group_names(force: bool = False) -> dict[int, str]:
    """群号 → 群名称（机器人所在的全部群，5 分钟缓存；机器人未连接时返回上次的结果）。"""
    now = time.time()
    if not force and _GROUP_NAMES["map"] and now - _GROUP_NAMES["ts"] < _GROUP_NAMES_TTL:
        return _GROUP_NAMES["map"]
    bot = _panel_bot()
    if bot is None:
        return _GROUP_NAMES["map"]
    try:
        rows = await bot.call_api("get_group_list")
    except Exception:
        return _GROUP_NAMES["map"]
    names = {int(g["group_id"]): str(g.get("group_name") or "") for g in rows if g.get("group_id")}
    _GROUP_NAMES.update(ts=now, map=names)
    return names


def _scan_hourly(log_dir: Path, day: str, group_id: int | None) -> list[int]:
    """扫描当天 jsonl 日志，按小时统计消息数（在线程中执行，避免阻塞事件循环）。"""
    hours = [0] * 24
    pattern = (
        f"group-{group_id}-{day}.jsonl" if group_id is not None else f"group-*-{day}.jsonl"
    )
    for path in sorted(log_dir.glob(pattern)):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = str(rec.get("time", ""))
                    if len(ts) >= 13 and ts[11:13].isdigit():
                        hour = int(ts[11:13])
                        if 0 <= hour < 24:
                            hours[hour] += 1
        except OSError:
            logger.warning(f"读取日志失败，跳过：{path}")
    return hours


# ---------------------------------------------------------------- 视图数据


async def _status_payload() -> dict[str, Any]:
    from src.plugins import reply as reply_plugin

    cfg = get_config()
    store = get_store()
    items = reply_plugin.get_items()
    reply_enabled = await store.get_kv("reply_enabled")
    welcome_enabled = await store.get_kv("welcome_enabled")
    link_preview_enabled = await store.get_kv("link_preview_enabled")
    vision_guard_enabled = await store.get_kv("vision_guard_enabled")
    proactive_enabled = await store.get_kv("proactive_enabled")
    blog_enabled = await store.get_kv("blog_enabled")
    log_dir = cfg.xingchao_log_dir
    log_files = sorted(log_dir.glob("group-*.jsonl")) if log_dir.exists() else []
    names = sorted(
        p.name
        for p in get_loaded_plugins()
        if p.module is not None and p.module.__name__.startswith("src.plugins")
    )
    return {
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
        "plugins": names,
        "whitelist": sorted(merged_whitelist()),
        "disabled_groups": sorted(disabled_groups()),
        "replies": len(items),
        "reply_enabled": reply_enabled != "false",
        "welcome_enabled": welcome_enabled != "false",
        "link_preview_enabled": link_preview_enabled != "false",
        "vision_guard_enabled": vision_guard_enabled != "false",
        "proactive_enabled": proactive_enabled != "false",
        "blog_enabled": blog_enabled != "false",
        "log_files": [f.name for f in log_files],
        "today": datetime.now().astimezone().strftime("%Y-%m-%d"),
    }
