"""申诉处理：管理员在 QQ 内审查并处理违规申诉（面板「违规记录」页走同一套逻辑）。

流程：用户私聊机器人申诉（sensitive.py 的 _handle_appeal）→ 机器人把申诉连同编号、
被处罚的原文发给所有超管 → 超管在 QQ 里用指令处理，或去面板处理，效果一致。

- 原文：punishments 只存 message_id，不存聊天内容；通知时按 message_id 回读
  data/logs/group-{群}-{日期}.jsonl（logger 已在记录）。图片/二维码类违规无文本原文。
- 指令（仅超管，群内或私聊均可）：
  /申诉列表、/申诉查看 <编号>、/申诉通过 <编号> [备注]、/申诉驳回 <编号> [理由]
- 处理后：通知申诉人（附备注/理由），并告知其他超管「已由某某处理」，避免重复处理
- 催办：申诉超过 REMIND_HOURS 小时仍未处理，提醒超管一次
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from nonebot import get_bots, get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import SUPERUSER
from src.store import get_store

REMIND_HOURS = 12
REMIND_SCAN_SECONDS = 600
ORIGINAL_MAX = 300

ACTION_LABEL = {
    "recall": "已撤回消息",
    "mute": "已禁言",
    "recall_mute": "已撤回并禁言",
    "kick": "已踢出群聊",
}
SOURCE_LABEL = {"sensitive": "敏感词", "vision": "图片审核", "qrcode": "二维码引流"}

_worker_started = False


def read_original_text(log_dir: Path, group_id: int, message_id: int | None, ts: str) -> str | None:
    """按 message_id 回读群消息日志；找不到返回 None。"""
    if message_id is None:
        return None
    path = Path(log_dir) / f"group-{group_id}-{ts[:10]}.jsonl"
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if f'"message_id": {message_id}' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("message_id") == message_id:
                    return str(rec.get("raw_plain", ""))
    except OSError:
        return None
    return None


async def original_block(rec: dict) -> str:
    """被处罚内容的展示文案。"""
    if rec["source"] != "sensitive":
        detail = rec.get("reason") or ""
        return f"（{SOURCE_LABEL.get(rec['source'], rec['source'])}：图片内容未保存）" + (
            f"\n依据：{detail[:120]}" if detail else ""
        )
    text = await asyncio.to_thread(
        read_original_text, get_config().xingchao_log_dir, rec["group_id"], rec.get("message_id"), rec["ts"]
    )
    if not text and "图片文字:" in (rec.get("reason") or ""):
        ocr = rec["reason"].split("图片文字:", 1)[1].split(" | ")[0].strip()
        return f"（图片，识别出的文字：{ocr}）"
    if text is None:
        return "（未找到原文：旧记录未保存消息编号，或日志已被清理）"
    return text[:ORIGINAL_MAX] + ("…" if len(text) > ORIGINAL_MAX else "") if text else "（消息无文本内容）"


def action_desc(rec: dict) -> str:
    desc = ACTION_LABEL.get(rec["action"], rec["action"])
    if rec["action"] in ("mute", "recall_mute") and rec.get("mute_minutes"):
        desc += f" {rec['mute_minutes']} 分钟"
    return desc


async def format_notice(rec: dict, appeal_text: str, *, header: str = "📮 违规申诉") -> str:
    """发给超管的申诉通知（含编号、原文与处理指令）。"""
    pid = rec["id"]
    return (
        f"{header} #{pid}\n"
        f"QQ: {rec['user_id']}　群: {rec['group_id']}\n"
        f"来源: {SOURCE_LABEL.get(rec['source'], rec['source'])}　命中: {rec['word']}\n"
        f"处罚: {action_desc(rec)}（{rec['ts'][:16].replace('T', ' ')}）\n"
        f"原文: {await original_block(rec)}\n"
        f"申诉理由: {appeal_text[:200]}\n"
        f"—— 处理：/申诉通过 {pid} [备注]　/申诉驳回 {pid} [理由]（也可在面板「违规记录」页处理）"
    )


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def notify_superusers(bot: Bot, text: str, exclude: int | None = None) -> None:
    for uid in _superuser_ids():
        if uid == exclude:
            continue
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"申诉通知超管 {uid} 失败")


async def resolve_appeal(
    bot: Bot | None, pid: int, approve: bool, note: str = "", operator: str = "面板",
    operator_uid: int | None = None,
) -> tuple[bool, str]:
    """处理申诉（QQ 指令与面板共用）。返回 (是否成功, 给处理人的说明)。"""
    store = get_store()
    record = await store.resolve_appeal(pid, approve, note=note, operator=operator)
    if record is None:
        return False, "该申诉不存在或已处理"
    msg = f"申诉 #{pid} 已{'通过' if approve else '驳回'}"
    if approve:
        if record["action"] in ("mute", "recall_mute"):
            if bot is None:
                msg += "，但机器人未连接，未能解除禁言"
            else:
                try:
                    await bot.call_api(
                        "set_group_ban", group_id=record["group_id"],
                        user_id=record["user_id"], duration=0,
                    )
                    msg += "，并已解除禁言"
                except Exception as e:
                    msg += f"，但解除禁言失败：{e}"
        elif record["action"] == "kick":
            msg += "（原处罚为踢出群聊，无法自动恢复，请手动邀请其回群）"
        else:
            msg += "（原处罚为撤回，无需解除禁言）"
    if bot is None:
        return True, msg + "（机器人未连接，未能通知申诉人与其他超管）"

    from src.plugins.sensitive import send_private

    verdict = "通过" if approve else "被驳回"
    text = f"你关于「{record['word']}」的申诉已{verdict}。"
    if note:
        text += f"\n管理员{'备注' if approve else '说明'}：{note}"
    if not await send_private(bot, record["user_id"], text, record["group_id"]):
        msg += "（但未能通知申诉人，请手动联系）"
    await notify_superusers(
        bot,
        f"ℹ️ 申诉 #{pid}（QQ {record['user_id']}）已由 {operator} {'通过' if approve else '驳回'}"
        + (f"：{note}" if note else ""),
        exclude=operator_uid,
    )
    return True, msg


# ---------------------------------------------------------------- 催办

async def remind_tick(bot: Bot, now: datetime | None = None) -> int:
    """对超过 REMIND_HOURS 仍未处理、且未催办过的申诉催办一次；返回催办条数。"""
    now = now or datetime.now().astimezone()
    stale = []
    for a in await get_store().pending_appeals():
        if a["reminded"] or not a["appeal_ts"]:
            continue
        try:
            age = (now - datetime.fromisoformat(a["appeal_ts"])).total_seconds()
        except ValueError:
            continue
        if age >= REMIND_HOURS * 3600:
            stale.append(a)
    if not stale:
        return 0
    lines = [f"⏰ 有 {len(stale)} 条申诉超过 {REMIND_HOURS} 小时未处理："]
    lines += [f"  #{a['id']} QQ {a['user_id']}（群 {a['group_id']}）命中「{a['word']}」" for a in stale]
    lines.append("用 /申诉查看 <编号> 查看详情，或在面板「违规记录」页处理。")
    await notify_superusers(bot, "\n".join(lines))
    for a in stale:
        await get_store().mark_appeal_reminded(a["id"])
    return len(stale)


async def _worker() -> None:
    logger.info("申诉催办调度器已启动")
    while True:
        try:
            bot = next(iter(get_bots().values()), None)
            if bot is not None:
                await remind_tick(bot)  # type: ignore[arg-type]
        except Exception:
            logger.exception("申诉催办扫描异常")
        await asyncio.sleep(REMIND_SCAN_SECONDS)


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())


# ---------------------------------------------------------------- 指令（仅超管）

list_cmd = on_command("申诉列表", aliases={"appeals"}, rule=SUPERUSER, priority=1, block=True)
view_cmd = on_command("申诉查看", rule=SUPERUSER, priority=1, block=True)
accept_cmd = on_command("申诉通过", aliases={"申诉同意"}, rule=SUPERUSER, priority=1, block=True)
reject_cmd = on_command("申诉驳回", aliases={"申诉拒绝"}, rule=SUPERUSER, priority=1, block=True)


def _parse(args: Message) -> tuple[int | None, str]:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if not parts or not parts[0].lstrip("#").isdigit():
        return None, ""
    return int(parts[0].lstrip("#")), (parts[1].strip() if len(parts) > 1 else "")


@list_cmd.handle()
async def handle_list(matcher) -> None:
    pending = await get_store().pending_appeals()
    if not pending:
        await matcher.send("当前没有待处理的申诉。")
        return
    lines = [f"待处理申诉（{len(pending)}）："]
    for a in pending:
        lines.append(
            f"  #{a['id']} QQ {a['user_id']}（群 {a['group_id']}）「{a['word']}」"
            f" {a['appeal_ts'][5:16].replace('T', ' ')}\n    {a['appeal_text'][:40]}"
        )
    lines.append("/申诉查看 <编号> 查看详情与原文")
    await matcher.send("\n".join(lines))


@view_cmd.handle()
async def handle_view(matcher, args: Message = CommandArg()) -> None:
    pid, _ = _parse(args)
    if pid is None:
        await matcher.send("用法：/申诉查看 <编号>")
        return
    rec = await get_store().get_punishment(pid)
    if rec is None or rec["appeal_status"] == "none":
        await matcher.send(f"没有编号为 #{pid} 的申诉。")
        return
    status = {"pending": "待处理", "accepted": "已通过", "rejected": "已驳回"}[rec["appeal_status"]]
    text = await format_notice(rec, rec["appeal_text"], header=f"📮 违规申诉（{status}）")
    if rec["appeal_status"] != "pending":
        text = text.rsplit("\n—— ", 1)[0]
        text += f"\n处理人: {rec['resolved_by'] or '未知'}" + (
            f"\n备注: {rec['appeal_note']}" if rec["appeal_note"] else ""
        )
    await matcher.send(text)


async def _do_resolve(matcher, bot: Bot, event: MessageEvent, args: Message, approve: bool) -> None:
    pid, note = _parse(args)
    if pid is None:
        word = "通过" if approve else "驳回"
        await matcher.send(f"用法：/申诉{word} <编号> [{'备注' if approve else '理由'}]")
        return
    ok, msg = await resolve_appeal(
        bot, pid, approve, note, operator=f"QQ {event.user_id}", operator_uid=event.user_id
    )
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)


@accept_cmd.handle()
async def handle_accept(matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _do_resolve(matcher, bot, event, args, True)


@reject_cmd.handle()
async def handle_reject(matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _do_resolve(matcher, bot, event, args, False)
