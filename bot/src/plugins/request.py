"""入群申请智能审批 + 退群群内播报。

入群申请（GroupRequestEvent，request_type=group）处理链：
  1. AI 判断（首选）：把验证问题与申请人回答交给 LLM，输出 JSON 决定
     approve / reject / manual（转人工）
  2. 程序规则兜底（AI 未配置/失败/转人工时）：
     - 回答含关键词（github、代码托管、开源 等）→ 通过
     - 回答为空或过短 → 转人工
     - 其余 → 转人工
  3. 转人工：通知所有超管私聊，携带序号；/approve <序号> [理由]、/reject <序号> [理由]
     或在面板操作（凭证 flag 保存在内存，重启失效，QQ 侧申请过期同样失效）

退群（GroupDecreaseNoticeEvent）：群内播报（可开关），机器人被踢（kick_me）通知超管。
配置均持久化 SQLite kv，面板「加群审批」页可改，即时生效。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from nonebot import get_driver, on_command, on_notice, on_request
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupDecreaseNoticeEvent,
    GroupRequestEvent,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER
from src.store import get_store

# ---------------------------------------------------------------- 配置（kv）

DEFAULTS: dict[str, Any] = {
    "join_mode": "ai",  # ai | manual | auto_approve | auto_reject
    "join_question": "GitHub是干什么的？",
    "join_fallback": "manual",  # AI 不可用时的兜底：manual | approve | reject
    "join_keywords": "github,git,代码托管,开源,托管平台,版本控制,程序员,代码仓库",
    "leave_report": True,  # 退群群内播报开关
}

_PENDING: dict[int, dict[str, Any]] = {}
_PENDING_SEQ = [0]


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw != "false"
    return raw


async def _group_overrides() -> dict[int, dict[str, Any]]:
    raw = await get_store().get_kv("join_group_config")
    if not raw:
        return {}
    try:
        return {int(g): v for g, v in json.loads(raw).items()}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


async def save_group_override(group_id: int, fields: dict[str, Any]) -> None:
    overrides = await _group_overrides()
    overrides[group_id] = fields
    await get_store().set_kv("join_group_config", json.dumps(overrides, ensure_ascii=False))


async def clear_group_override(group_id: int) -> bool:
    overrides = await _group_overrides()
    if group_id not in overrides:
        return False
    overrides.pop(group_id)
    await get_store().set_kv("join_group_config", json.dumps(overrides, ensure_ascii=False))
    return True


async def join_config(group_id: int | None = None) -> dict[str, Any]:
    """生效配置 = 全局默认 + 该群覆盖（group_id 为 None 时返回全局）。"""
    global_cfg = {k: await _kv(k) for k in DEFAULTS}
    if group_id is None:
        return global_cfg
    overrides = await _group_overrides()
    override = overrides.get(group_id, {})
    merged = {**global_cfg, **{k: v for k, v in override.items() if k in DEFAULTS}}
    # 布尔字段收敛（覆盖值可能是 "true"/"false" 字符串）
    lr = merged["leave_report"]
    merged["leave_report"] = lr is True or (isinstance(lr, str) and lr != "false")
    return merged


# ---------------------------------------------------------------- 工具函数


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def _notify_superusers(bot: Bot, text: str) -> None:
    for uid in _superuser_ids():
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"通知超管 {uid} 失败")


def _rule_verdict(comment: str, keywords: str) -> str:
    """程序规则兜底：返回 approve / manual。"""
    text = comment.strip().lower()
    if not text or len(text) < 2:
        return "manual"
    for kw in keywords.split(","):
        kw = kw.strip().lower()
        if kw and kw in text:
            return "approve"
    return "manual"


async def _ai_verdict(bot: Bot, question: str, comment: str) -> str | None:
    """AI 判断；返回 'approve' / 'reject' / 'manual'，失败返回 None。"""
    from src.plugins import ai as ai_plugin

    client = await ai_plugin.get_client()
    if client is None:
        return None
    cfg = await ai_plugin.ai_config()
    system = (
        "你是QQ群加群审批助手。加群验证问题是：「" + question + "」。"
        "请判断申请人的回答是否与问题的正确含义相符（合理即可，不要求逐字准确）。"
        '只输出一个 JSON 对象：{"decision":"approve"|"reject"|"manual","reason":"10字以内中文理由"}，'
        "不要输出其他内容。回答正确或基本合理 → approve；明显错误/答非所问/敷衍 → reject；"
        "无法判断 → manual。"
    )
    try:
        completion = await client.chat.completions.create(
            model=cfg["ai_model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"申请人回答：{comment}"},
            ],
            temperature=0.1,
        )
        raw = (completion.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        data = json.loads(m.group())
        decision = data.get("decision")
        if decision not in ("approve", "reject", "manual"):
            return None
        reason = str(data.get("reason", ""))[:50]
        return json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False)
    except Exception:
        logger.exception("AI 审批判断失败")
        return None


async def _handle_request(bot: Bot, event: GroupRequestEvent) -> None:
    cfg = await join_config(event.group_id)
    comment = (event.comment or "").strip()
    mode = cfg["join_mode"]

    decision: str | None = None
    reason = ""
    ai_note = ""

    if mode == "auto_approve":
        decision, reason = "approve", "自动通过模式"
    elif mode == "auto_reject":
        decision, reason = "reject", "自动拒绝模式"
    elif mode == "ai":
        verdict = await _ai_verdict(bot, cfg["join_question"], comment)
        if verdict is None:
            ai_note = "（AI 不可用，走规则兜底）"
        else:
            data = json.loads(verdict)
            decision, reason = data["decision"], data["reason"]
        if decision is None:
            decision = _rule_verdict(comment, str(cfg["join_keywords"]))
            if decision == "approve":
                reason = "规则兜底：回答含关键词"
        if decision == "manual":
            decision = None  # 转人工

    if decision is None:
        # 转人工：记录待审批并通知超管
        _PENDING_SEQ[0] += 1
        seq = _PENDING_SEQ[0]
        _PENDING[seq] = {
            "flag": event.flag,
            "sub_type": event.sub_type,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "comment": comment,
            "time": time.time(),
        }
        logger.info(f"入群申请转人工 #{seq}：group={event.group_id} user={event.user_id}")
        await _notify_superusers(
            bot,
            f"📥 入群申请 #{seq}（待审批）\n"
            f"群号: {event.group_id}\n"
            f"QQ: {event.user_id}\n"
            f"回答: {comment or '（无）'}{ai_note}\n"
            f"通过: /通过 {seq}　拒绝: /拒绝 {seq} [理由]",
        )
        return

    # 自动执行
    approve = decision == "approve"
    try:
        await bot.call_api(
            "set_group_add_request",
            flag=event.flag,
            sub_type=event.sub_type,
            approve=approve,
            reason=reason if not approve else "",
        )
    except Exception:
        logger.exception("处理入群申请失败")
        return
    logger.info(f"入群申请自动{('通过' if approve else '拒绝')}：user={event.user_id} 理由={reason}")
    await _notify_superusers(
        bot,
        f"{'✅ 已通过' if approve else '❌ 已拒绝'}入群申请\n"
        f"群号: {event.group_id}｜QQ: {event.user_id}\n"
        f"回答: {comment or '（无）'}\n理由: {reason}{ai_note}",
    )


# ---------------------------------------------------------------- 事件处理

join_request = on_request(priority=1, block=False)


@join_request.handle()
async def handle_join_request(bot: Bot, event: GroupRequestEvent) -> None:
    if event.request_type != "group":
        return
    await _handle_request(bot, event)


leave_notice = on_notice(rule=GROUP_WHITELIST, priority=3, block=False)


@leave_notice.handle()
async def handle_leave(bot: Bot, event: GroupDecreaseNoticeEvent) -> None:
    cfg = await join_config(event.group_id)
    if not cfg["leave_report"]:
        return
    if event.sub_type == "kick_me":
        await _notify_superusers(
            bot, f"⚠️ 机器人被移出群 {event.group_id}（operator={event.operator_id}）"
        )
        return
    action = "被移出" if event.sub_type == "kick" else "退出"
    nickname = str(event.user_id)
    try:
        info = await bot.call_api("get_stranger_info", user_id=event.user_id)
        if info.get("nickname"):
            nickname = info["nickname"]
    except Exception:
        pass
    try:
        await bot.call_api(
            "send_group_msg",
            group_id=event.group_id,
            message=f"👋 成员 {nickname}（{event.user_id}）已{action}本群",
        )
    except MatcherException:
        raise
    except Exception:
        logger.exception(f"退群播报失败：group={event.group_id}")


# ---------------------------------------------------------------- /approve /reject

approve_cmd = on_command("通过", rule=SUPERUSER, priority=1, block=True)
approve_cmd_en = on_command("approve", rule=SUPERUSER, priority=1, block=True)
reject_cmd = on_command("拒绝", rule=SUPERUSER, priority=1, block=True)
reject_cmd_en = on_command("reject", rule=SUPERUSER, priority=1, block=True)


async def _resolve(bot: Bot, matcher: Matcher, event: MessageEvent,
                   seq: int, approve: bool, reason: str) -> None:
    req = _PENDING.get(seq)
    if req is None:
        await matcher.send(f"没有找到申请 #{seq}（可能已处理、过期或重启丢失）。")
        return
    try:
        await bot.call_api(
            "set_group_add_request",
            flag=req["flag"],
            sub_type=req["sub_type"],
            approve=approve,
            reason=reason,
        )
    except Exception as e:
        logger.exception("审批入群申请失败")
        await matcher.send(f"审批失败：{e}（凭证可能已过期，请让对方重新申请）")
        _PENDING.pop(seq, None)
        return
    _PENDING.pop(seq, None)
    verb = "通过" if approve else "拒绝"
    await matcher.send(f"已{verb}申请 #{seq}（QQ {req['user_id']}）。")
    await _notify_superusers(
        bot,
        f"{'✅' if approve else '❌'} 入群申请 #{seq} 已被 {event.user_id} {verb}"
        + (f"（理由：{reason}）" if reason else ""),
    )


async def _approve_handler(bot: Bot, event: MessageEvent, matcher: Matcher,
                           args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await matcher.send("用法：/通过 <序号>（或 /approve <序号>）")
        return
    await _resolve(bot, matcher, event, int(parts[0]), True,
                   parts[1].strip() if len(parts) > 1 else "")


async def _reject_handler(bot: Bot, event: MessageEvent, matcher: Matcher,
                          args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await matcher.send("用法：/拒绝 <序号> [理由]（或 /reject <序号> [理由]）")
        return
    await _resolve(bot, matcher, event, int(parts[0]), False,
                   parts[1].strip() if len(parts) > 1 else "不符合入群要求")


approve_cmd.append_handler(_approve_handler)
approve_cmd_en.append_handler(_approve_handler)
reject_cmd.append_handler(_reject_handler)
reject_cmd_en.append_handler(_reject_handler)


# ---------------------------------------------------------------- /pending

pending_cmd = on_command("pending", rule=SUPERUSER, priority=1, block=True)


@pending_cmd.handle()
async def handle_pending(matcher: Matcher) -> None:
    if not _PENDING:
        await matcher.send("当前没有待审批的入群申请。")
        return
    lines = ["待审批入群申请："]
    for seq, req in sorted(_PENDING.items()):
        lines.append(f"  #{seq} 群{req['group_id']} QQ{req['user_id']} 回答:{req['comment'] or '（无）'}")
    await matcher.send("\n".join(lines))
