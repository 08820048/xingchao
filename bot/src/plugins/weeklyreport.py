"""群活跃周报：每周定时向白名单群播报前一周活跃数据（消息量、环比、参与人数、最活跃日、Top3）。

- 配置（kv，面板「定时任务」页可改）：开关 report_enabled、星期 report_weekday、
  时间 report_time、范围 report_scope（all=全部白名单群 / 指定群号）
- 调度：后台协程每 30 秒扫描，按北京时间（TZ=Asia/Shanghai）触发；
  每个群每天最多播报一次（kv 去重）
- 数据：msg_stat / msg_stat_user 聚合（不含聊天内容）；Top3 昵称通过群成员 API 获取
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from nonebot import get_bot, get_driver
from nonebot.log import logger

from src.permission import merged_whitelist
from src.store import get_store

DEFAULTS: dict[str, Any] = {
    "report_enabled": False,
    "report_weekday": 1,  # 0=周一 … 6=周日
    "report_time": "09:00",
    "report_scope": "all",  # all 或群号
}

WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_worker_started = False


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    if raw is None:
        return DEFAULTS[key]  # 注意：bool 默认值不能走下面的字符串比较分支
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


async def report_config() -> dict[str, Any]:
    return {k: await _kv(k) for k in DEFAULTS}


def _fmt_delta(total: int, prev: int) -> str:
    if prev <= 0:
        return "（前一周无数据）"
    pct = round((total - prev) / prev * 100)
    if pct == 0:
        return "（较前一周持平）"
    return f"（较前一周 {'+' if pct > 0 else ''}{pct}%）"


async def _member_name(bot, group_id: int, user_id: int) -> str:
    try:
        info = await bot.call_api(
            "get_group_member_info", group_id=group_id, user_id=user_id, no_cache=True
        )
        return str(info.get("card") or info.get("nickname") or user_id)
    except Exception:
        return str(user_id)


async def build_report(group_id: int) -> str | None:
    """生成单群周报文本（前一周，即 7 天前 ~ 昨天）；无数据返回 None。"""
    store = get_store()
    today = datetime.now().astimezone().date()
    start = (today - timedelta(days=7)).isoformat()
    end = (today - timedelta(days=1)).isoformat()
    prev_start = (today - timedelta(days=14)).isoformat()
    prev_end = (today - timedelta(days=8)).isoformat()

    series = await store.get_day_series(start, end, group_id)
    total = sum(p[1] for p in series)
    if total <= 0:
        return None
    prev_total = sum(
        p[1] for p in await store.get_day_series(prev_start, prev_end, group_id)
    )
    users = await store.get_range_users(start, end, group_id)
    top = await store.get_top_users_range(start, end, group_id, limit=3)
    busiest = max(series, key=lambda p: p[1])

    lines = [
        f"📊 群活跃周报（{start[5:]} ~ {end[5:]}）",
        f"消息 {total} 条{_fmt_delta(total, prev_total)}",
        f"参与 {users} 人 · 最活跃：{busiest[0][5:]}（{busiest[1]} 条）",
    ]
    if top:
        try:
            bot = get_bot()
        except Exception:
            bot = None
        names = []
        for uid, cnt in top:
            name = await _member_name(bot, group_id, uid) if bot else str(uid)
            names.append(f"{name} {cnt}")
        lines.append("🏆 发言榜：" + " · ".join(names))
    return "\n".join(lines)


async def _send_reports() -> None:
    cfg = await report_config()
    scope = str(cfg["report_scope"])
    groups = sorted(merged_whitelist()) if scope == "all" else [int(scope)]
    day_key = datetime.now().astimezone().strftime("%Y-%m-%d")
    bot = get_bot()
    for gid in groups:
        key = f"report_last_{gid}"
        if await get_store().get_kv(key) == day_key:
            continue
        text = await build_report(gid)
        if text is None:
            logger.info(f"周报跳过（无数据）：群 {gid}")
        else:
            try:
                await bot.call_api("send_group_msg", group_id=gid, message=text)
                logger.info(f"周报已发送：群 {gid}")
            except Exception:
                logger.exception(f"周报发送失败：群 {gid}")
                continue
        await get_store().set_kv(key, day_key)


async def _worker() -> None:
    logger.info("群活跃周报调度器已启动（每 30 秒扫描）")
    while True:
        try:
            cfg = await report_config()
            now = datetime.now()
            if (
                cfg["report_enabled"]
                and now.weekday() == int(cfg["report_weekday"])
                and now.strftime("%H:%M") == str(cfg["report_time"])
            ):
                await _send_reports()
        except Exception:
            logger.exception("周报扫描异常")
        await asyncio.sleep(30)


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())
