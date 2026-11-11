"""能力自检：探测当前连接的 NapCat 到底支持哪些 QQ 能力（只调用只读接口，不会改动任何东西）。

- 指令（仅超管）：/能力自检 —— 逐个试探并返回 ✅支持 / ❌不支持 / ⚠️存在但本次调用报错
- 目的：NapCat 版本不同、接口会有差异；新功能（精华、收藏表情包、OCR、历史消息……）上线前先看清楚
  自己的环境支持什么，而不是凭文档猜
- 结果会存入 kv capabilities_last，面板/其他功能可据此降级
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.permission import SUPERUSER, merged_whitelist
from src.store import get_store

# 1x1 透明 PNG，用来探测 OCR 接口是否存在（不依赖任何真实图片）
_TINY_PNG = (
    "base64://iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _n(v: Any) -> int:
    return len(v) if isinstance(v, (list, dict)) else 0


# (名称, 接口, 参数构造(群号)->dict, 结果摘要(result)->str, 说明)
Probe = tuple[str, str, Callable[[int], dict], Callable[[Any], str], str]
PROBES: list[Probe] = [
    ("版本信息", "get_version_info", lambda g: {}, lambda r: f"{r.get('app_name', '')} {r.get('app_version', '')}".strip(), "基础"),
    ("运行状态", "get_status", lambda g: {}, lambda r: "online" if (r or {}).get("online") else "离线?", "基础"),
    ("能发图片", "can_send_image", lambda g: {}, lambda r: str((r or {}).get("yes")), "基础"),
    ("能发语音", "can_send_record", lambda g: {}, lambda r: str((r or {}).get("yes")), "基础"),
    ("群列表", "get_group_list", lambda g: {}, lambda r: f"{_n(r)} 个群", "面板群名称"),
    ("精华消息", "get_essence_msg_list", lambda g: {"group_id": g}, lambda r: f"{_n(r)} 条", "设为精华"),
    ("收藏表情包", "fetch_custom_face", lambda g: {"count": 5}, lambda r: f"{_n(r)} 张", "表情包库"),
    ("图片文字识别", "ocr_image", lambda g: {"image": _TINY_PNG}, lambda r: "ok", "图片文字过审核"),
    ("群历史消息", "get_group_msg_history", lambda g: {"group_id": g, "count": 3}, lambda r: f"{_n((r or {}).get('messages'))} 条", "重启后回填"),
    ("群荣誉", "get_group_honor_info", lambda g: {"group_id": g, "type": "talkative"}, lambda r: "ok", "龙王等"),
    ("@全体剩余次数", "get_group_at_all_remain", lambda g: {"group_id": g}, lambda r: str((r or {}).get("remain_at_all_count_for_uin", "?")), "定时任务@全体"),
    ("群禁言列表", "get_group_shut_list", lambda g: {"group_id": g}, lambda r: f"{_n(r)} 人", "管理"),
    ("群文件", "get_group_root_files", lambda g: {"group_id": g}, lambda r: f"{_n((r or {}).get('files'))} 个", "群文件问答"),
    ("英译中", "translate_en2zh", lambda g: {"words": ["hello"]}, lambda r: "ok", "免费翻译"),
    ("链接安全检查", "check_url_safely", lambda g: {"url": "https://xingchao.dev"}, lambda r: "ok", "链接解读"),
]

OK, UNSUPPORTED, ERROR = "ok", "unsupported", "error"


def classify_failure(exc: Exception) -> str:
    """区分"接口不存在"和"接口存在但这次调用失败（参数/权限/群不对）"。"""
    info = getattr(exc, "info", None) or {}
    text = (str(exc) + " " + json.dumps(info, ensure_ascii=False, default=str)).lower()
    retcode = info.get("retcode") if isinstance(info, dict) else None
    if retcode == 1404 or any(k in text for k in ("不支持", "unsupported", "unknown action", "not support", "未知的api")):
        return UNSUPPORTED
    return ERROR


async def probe(bot: Bot, group_id: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for name, api, params, summarize, purpose in PROBES:
        try:
            raw = await bot.call_api(api, **params(group_id))
            try:
                detail = summarize(raw)
            except Exception:
                detail = "ok"
            results.append({"name": name, "api": api, "status": OK, "detail": detail, "purpose": purpose})
        except Exception as e:
            status = classify_failure(e)
            msg = str(getattr(e, "info", None) or e)[:60]
            results.append({"name": name, "api": api, "status": status, "detail": msg, "purpose": purpose})
    return results


def format_report(results: list[dict[str, str]], group_id: int) -> str:
    icon = {OK: "✅", UNSUPPORTED: "❌", ERROR: "⚠️"}
    lines = [f"🔍 能力自检（以群 {group_id} 为探测对象，只读）"]
    for r in results:
        lines.append(f"{icon[r['status']]} {r['name']}（{r['api']}）" + (f" {r['detail']}" if r["detail"] else ""))
    bad = [r for r in results if r["status"] == UNSUPPORTED]
    warn = [r for r in results if r["status"] == ERROR]
    if bad:
        lines.append("❌ 不支持：" + "、".join(r["purpose"] for r in bad) + " 相关功能会自动降级")
    if warn:
        lines.append("⚠️ 接口存在但本次调用报错（多半是机器人在该群没有对应权限，或群里没有数据），实际使用时再看")
    return "\n".join(lines)


async def run_and_store(bot: Bot, group_id: int) -> str:
    results = await probe(bot, group_id)
    try:
        await get_store().set_kv(
            "capabilities_last",
            json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "results": {r["api"]: r["status"] for r in results}}, ensure_ascii=False),
        )
    except Exception:
        logger.exception("保存能力自检结果失败")
    return format_report(results, group_id)


async def supported(api: str) -> bool | None:
    """最近一次自检里该接口是否被支持；没跑过自检返回 None（调用方按"尝试一下"处理）。"""
    raw = await get_store().get_kv("capabilities_last")
    if not raw:
        return None
    try:
        status = json.loads(raw)["results"].get(api)
    except (ValueError, KeyError, TypeError):
        return None
    return None if status is None else status != UNSUPPORTED


cap_cmd = on_command("能力自检", aliases={"capabilities"}, rule=SUPERUSER, priority=1, block=True)


@cap_cmd.handle()
async def handle_cap(matcher: Matcher, bot: Bot, event: MessageEvent) -> None:
    if isinstance(event, GroupMessageEvent):
        gid = event.group_id
    else:
        groups = sorted(merged_whitelist())
        if not groups:
            await matcher.send("白名单为空，没有可用于探测的群。")
            return
        gid = groups[0]
    await matcher.send("正在探测，稍等几秒…")
    await matcher.send(await run_and_store(bot, gid))
