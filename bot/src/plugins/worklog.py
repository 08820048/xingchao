"""工作台账：把「机器人为这个群做了什么」摊开给人看。

为什么要有：删广告、拦违规码、答问题、翻旧消息——这些活干得越多，群友越看不见。
结果就是「它好像啥也没干」，只剩签到有存在感。本模块按周汇总机器人实际做过的事，
用具体数字把价值显性化。

数据来源（都是已有记录，不新增隐私面）：
- punishments 表：敏感词 / 违规图片 / 引流二维码 / 广告命中（按 ts 区间统计）
- action_stat 表：AI 回答、知识命中、回忆命中、链接解读、精华（只计数，不含内容）
- msg_stat 表：群活跃度，用来给数字一个参照

输出：`/台账 [天数]` 随时看；面板「工作台账」页看汇总；
后台每周定时播报（默认关，开了才会发到群里）。

诚实原则：只报真实发生过的事，没做的就不写；一条都没有时说「这周很安静」，
不编数字、不夸大。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nonebot import get_bot, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.background import periodic
from src.permission import BASIC, merged_whitelist
from src.store import get_store

DEFAULTS: dict[str, Any] = {
    "worklog_enabled": True,      # 允许 /台账 查询与面板查看
    "worklog_weekly": False,      # 每周自动播报到群（默认关，避免又多一条刷屏）
    "worklog_weekday": 0,         # 0=周一
    "worklog_time": "10:00",
    "worklog_scope": "all",       # all 或群号
}

WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 处罚来源 → 展示名（按「拦住了什么」分类，而不是按代码模块）
MODERATION_SOURCES: dict[str, str] = {
    "sensitive": "违规言论",
    "vision": "违规图片",
    "qrcode": "引流二维码",
    "ad": "群内广告",
    "ocr": "图片里的违规文字",
}

# action_stat 动作 → 展示名
HELP_ACTIONS: dict[str, str] = {
    "ai_reply": "回答群友提问",
    "kb_answer": "命中群知识（省了重复回答）",
    "recall_query": "翻出以前的聊天",
    "link_preview": "解读链接",
}


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw == "true"
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


async def is_enabled() -> bool:
    return bool(await _kv("worklog_enabled"))


async def worklog_config() -> dict[str, Any]:
    return {k: await _kv(k) for k in DEFAULTS}


async def build_worklog(group_id: int | None = None, *, days: int = 7) -> str:
    """生成台账文本。group_id=None 表示全部白名单群合计。"""
    store = get_store()
    today = datetime.now().astimezone().date()
    start = today - timedelta(days=days)
    start_s, end_s = start.isoformat(), today.isoformat()
    prev_start = (start - timedelta(days=days)).isoformat()
    prev_end = (start - timedelta(days=1)).isoformat()

    punishments = await store.list_punishments(
        group_id=group_id, start=start_s, end=end_s, limit=5000
    )
    actions = await store.action_totals(start_s, end_s, group_id)
    prev_actions = await store.action_totals(prev_start, prev_end, group_id)

    scope = "全部群" if group_id is None else f"群 {group_id}"
    lines = [
        "🤖 星潮工作台账",
        f"🗓 {start_s[5:].replace('-', '/')} ~ {end_s[5:].replace('-', '/')}（{scope}）",
        "",
    ]
    has_content = False

    # 内容治理
    if punishments:
        has_content = True
        lines.append("「🛡 内容治理」")
        counts: dict[str, int] = {}
        for row in punishments:
            src = str(row.get("source") or "sensitive")
            counts[src] = counts.get(src, 0) + 1
        for source, label_text in MODERATION_SOURCES.items():
            n = counts.pop(source, 0)
            if n:
                lines.append(f"  · 拦下{label_text} {n} 次")
        for source, n in counts.items():
            lines.append(f"  · 拦下 {source} {n} 次")
        muted = sum(1 for p in punishments if int(p.get("mute_minutes") or 0) > 0)
        if muted:
            lines.append(f"  · 其中禁言 {muted} 人次")
        lines.append("")

    # 帮上忙的地方
    help_lines = [
        f"  · {label_text} {actions[key]} 次"
        for key, label_text in HELP_ACTIONS.items()
        if actions.get(key)
    ]
    extras = [(k, v) for k, v in actions.items() if k not in HELP_ACTIONS and v]
    for key, value in sorted(extras, key=lambda kv: -kv[1]):
        name = {"essence_set": "设为群精华"}.get(key, key)
        help_lines.append(f"  · {name} {value} 次")
    if help_lines:
        has_content = True
        lines.append("「💬 帮上忙的地方」")
        lines += help_lines
        prev_total = sum(prev_actions.values())
        cur_total = sum(actions.values())
        if prev_total:
            pct = round((cur_total - prev_total) / prev_total * 100)
            lines.append(
                f"  ↳ 合计 {cur_total} 次，"
                + ("持平" if pct == 0 else f"较上期 {'▲' if pct > 0 else '▼'} {abs(pct)}%")
            )
        lines.append("")

    if not has_content:
        lines.append("这段时间很安静，我没派上什么用场。有事可以直接 @ 我。")

    # 一句话点睛：知识命中 ≈ 省下的一次重复回答
    kb_hits = actions.get("kb_answer", 0)
    if kb_hits:
        if has_content and lines and lines[-1] != "":
            lines.append("")
        lines.append(f"光「群知识」这一项，就替人打了 {kb_hits} 遍同样的答案。")
    # 收尾：去掉尾部多余空行，正文之间最多留一个空行
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


async def group_worklog(group_id: int, *, days: int = 7) -> str:
    return await build_worklog(group_id, days=days)


# ---------------------------------------------------------------- 指令

worklog_cmd = on_command("台账", aliases={"worklog"}, rule=BASIC, priority=5, block=True)


@worklog_cmd.handle()
async def handle_worklog(event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    if not await is_enabled():
        await matcher.finish("工作台账当前已关闭。")
    raw = args.extract_plain_text().strip()
    days = 7
    if raw.isdigit():
        days = max(1, min(int(raw), 90))
    gid = getattr(event, "group_id", None)
    await matcher.send(await build_worklog(gid, days=days))


# ---------------------------------------------------------------- 每周播报

@periodic("工作台账播报", 30, wait_bot=False)
async def _scan(_bot) -> None:
    cfg = await worklog_config()
    if not cfg["worklog_weekly"]:
        return
    now = datetime.now()
    if now.weekday() != int(cfg["worklog_weekday"]) or now.strftime("%H:%M") != str(cfg["worklog_time"]):
        return
    today = now.astimezone().strftime("%Y-%m-%d")
    scope = str(cfg["worklog_scope"])
    groups = sorted(merged_whitelist()) if scope == "all" else [int(scope)]
    try:
        bot = get_bot()
    except Exception:
        logger.debug("工作台账播报跳过：没有可用 bot")
        return
    for gid in groups:
        key = f"worklog_last_{gid}"
        if await get_store().get_kv(key) == today:
            continue
        try:
            text = await build_worklog(gid, days=7)
            await bot.call_api("send_group_msg", group_id=gid, message=text)
            logger.info(f"工作台账已发送：群 {gid}")
        except Exception:
            logger.exception(f"工作台账发送失败：群 {gid}")
            continue
        await get_store().set_kv(key, today)


async def panel_payload() -> dict[str, Any]:
    store = get_store()
    today = datetime.now().astimezone().date()
    start_s = (today - timedelta(days=7)).isoformat()
    end_s = today.isoformat()
    return {
        **{k.replace("worklog_", ""): await _kv(k) for k in DEFAULTS},
        "groups": sorted(merged_whitelist()),
        "actions": await store.action_totals(start_s, end_s),
        "by_group": await store.action_by_group(start_s, end_s),
        "range": [start_s, end_s],
        "weekdays": WEEKDAY_NAME,
    }

