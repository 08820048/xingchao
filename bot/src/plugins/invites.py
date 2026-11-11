"""好友申请与拉群邀请：通知超管，一句话通过/拒绝。

- 以前没人处理：别人加机器人好友、把机器人拉进新群时它毫无反应（入群申请只处理白名单群里的）
- 现在：私聊通知所有超管（带序号与指令）；超管本人的好友申请 / 拉群邀请自动通过
- 指令（仅超管）：/邀请列表、/同意邀请 <序号>、/拒绝邀请 <序号> [理由]
- AI 工具：list_pending_invites、resolve_invite
- 待处理项只在内存里，QQ 侧凭证约 3 天内有效，过期会清理
- 说明：被拉进新群后该群还不在白名单里，机器人不会在群里做任何业务；
  要启用请再用 /group add <群号>
"""

from __future__ import annotations

import time
from typing import Any

from nonebot import get_driver, on_command, on_request
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, FriendRequestEvent, GroupRequestEvent, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import SUPERUSER

EXPIRE_SECONDS = 3 * 24 * 3600
_PENDING: dict[int, dict[str, Any]] = {}
_SEQ = [0]


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def _notify(bot: Bot, text: str, exclude: int | None = None) -> None:
    for uid in _superuser_ids():
        if uid == exclude:
            continue
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"邀请通知超管 {uid} 失败")


def _purge(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    for seq in [s for s, r in _PENDING.items() if now - r["time"] > EXPIRE_SECONDS]:
        _PENDING.pop(seq, None)


def add_pending(kind: str, user_id: int, flag: str, comment: str, group_id: int | None = None,
                *, now: float | None = None) -> int:
    """登记待处理项，返回序号。kind: friend / group_invite。"""
    now = now if now is not None else time.time()
    _purge(now)
    _SEQ[0] += 1
    _PENDING[_SEQ[0]] = {"kind": kind, "user_id": user_id, "flag": flag,
                         "comment": comment, "group_id": group_id, "time": now}
    return _SEQ[0]


def describe(seq: int, req: dict[str, Any], nickname: str = "") -> str:
    who = f"QQ {req['user_id']}" + (f"（{nickname}）" if nickname else "")
    if req["kind"] == "friend":
        head = f"👥 好友申请 #{seq}\n申请人：{who}\n验证信息：{req['comment'] or '（无）'}"
    else:
        head = f"📨 拉群邀请 #{seq}\n邀请人：{who}\n目标群：{req['group_id']}"
    return f"{head}\n通过：/同意邀请 {seq}　拒绝：/拒绝邀请 {seq} [理由]"


async def resolve(bot: Bot, seq: int, approve: bool, reason: str = "") -> tuple[bool, str]:
    req = _PENDING.get(seq)
    if req is None:
        return False, f"没有找到 #{seq}（可能已处理、已过期或重启丢失）"
    try:
        if req["kind"] == "friend":
            await bot.call_api("set_friend_add_request", flag=req["flag"], approve=approve, remark="")
        else:
            await bot.call_api("set_group_add_request", flag=req["flag"], sub_type="invite",
                               approve=approve, reason=reason)
    except Exception as e:
        _PENDING.pop(seq, None)
        return False, f"处理失败：{e}（凭证可能已过期，请让对方重新申请）"
    _PENDING.pop(seq, None)
    what = "好友申请" if req["kind"] == "friend" else f"拉群邀请（群 {req['group_id']}）"
    msg = f"已{'通过' if approve else '拒绝'}{what} #{seq}（QQ {req['user_id']}）"
    if approve and req["kind"] == "group_invite":
        msg += "。该群尚未加入白名单，要启用请发送 /白名单 添加 " + str(req["group_id"])
    return True, msg


def list_text() -> str:
    _purge()
    if not _PENDING:
        return "当前没有待处理的好友申请或拉群邀请。"
    lines = ["待处理："]
    for seq, r in sorted(_PENDING.items()):
        if r["kind"] == "friend":
            lines.append(f"  #{seq} 好友申请 QQ {r['user_id']}：{r['comment'] or '（无验证信息）'}")
        else:
            lines.append(f"  #{seq} 拉群邀请 QQ {r['user_id']} → 群 {r['group_id']}")
    return "\n".join(lines)


async def _nickname(bot: Bot, user_id: int) -> str:
    try:
        return str((await bot.call_api("get_stranger_info", user_id=user_id)).get("nickname") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------- 事件

async def _is_friend_request(event) -> bool:
    return isinstance(event, FriendRequestEvent)


async def _is_group_invite(event) -> bool:
    return isinstance(event, GroupRequestEvent) and event.sub_type == "invite"


friend_request = on_request(rule=_is_friend_request, priority=1, block=False)
group_invite = on_request(rule=_is_group_invite, priority=1, block=False)


@friend_request.handle()
async def handle_friend(bot: Bot, event: FriendRequestEvent) -> None:
    if event.user_id in _superuser_ids():
        try:
            await bot.call_api("set_friend_add_request", flag=event.flag, approve=True, remark="")
            logger.info(f"已自动通过超管 {event.user_id} 的好友申请")
        except Exception:
            logger.exception("自动通过超管好友申请失败")
        return
    seq = add_pending("friend", event.user_id, event.flag, (event.comment or "").strip())
    await _notify(bot, describe(seq, _PENDING[seq], await _nickname(bot, event.user_id)))


@group_invite.handle()
async def handle_invite(bot: Bot, event: GroupRequestEvent) -> None:
    if event.user_id in _superuser_ids():
        try:
            await bot.call_api("set_group_add_request", flag=event.flag, sub_type="invite", approve=True, reason="")
            logger.info(f"已自动通过超管 {event.user_id} 的拉群邀请：群 {event.group_id}")
        except Exception:
            logger.exception("自动通过超管拉群邀请失败")
        return
    seq = add_pending("group_invite", event.user_id, event.flag, "", event.group_id)
    await _notify(bot, describe(seq, _PENDING[seq], await _nickname(bot, event.user_id)))


# ---------------------------------------------------------------- 指令

list_cmd = on_command("邀请列表", rule=SUPERUSER, priority=1, block=True)
approve_cmd = on_command("同意邀请", aliases={"同意好友"}, rule=SUPERUSER, priority=1, block=True)
reject_cmd = on_command("拒绝邀请", aliases={"拒绝好友"}, rule=SUPERUSER, priority=1, block=True)


@list_cmd.handle()
async def handle_list(matcher: Matcher) -> None:
    await matcher.send(list_text())


async def _do(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message, approve: bool) -> None:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if not parts or not parts[0].lstrip("#").isdigit():
        await matcher.send(f"用法：/{'同意' if approve else '拒绝'}邀请 <序号>" + ("" if approve else " [理由]"))
        return
    seq = int(parts[0].lstrip("#"))
    ok, msg = await resolve(bot, seq, approve, parts[1].strip() if len(parts) > 1 else "")
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)
    if ok:
        await _notify(bot, f"ℹ️ #{seq} 已由 {event.user_id} {'通过' if approve else '拒绝'}", exclude=event.user_id)


@approve_cmd.handle()
async def handle_approve(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _do(matcher, bot, event, args, True)


@reject_cmd.handle()
async def handle_reject(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _do(matcher, bot, event, args, False)
