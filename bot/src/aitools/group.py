"""AI 工具：群信息、成员与群管理（禁言/踢人/名片/精华/公告/入群审核等）。"""

from __future__ import annotations

from datetime import datetime

from src.aitools.registry import _ai, _j, tool
from src.store import get_store


@tool(
    "get_group_info",
    "获取群聊信息（群名、成员数）",
    {"type": "object", "properties": {"group_id": {"type": "integer", "description": "群号，默认当前群"}}, "required": []},
    "all",
)
async def _t_group_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    info = await bot.call_api("get_group_info", group_id=int(group_id))
    return _j({"群号": info["group_id"], "群名": info.get("group_name"), "成员数": info.get("member_count"), "上限": info.get("max_member_count")})


@tool(
    "get_member_list",
    "获取群成员列表（QQ、昵称、群名片、角色）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_member_list(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号。"
    members = await bot.call_api("get_group_member_list", group_id=int(group_id))
    brief = [
        {"QQ": m["user_id"], "昵称": m.get("nickname"), "名片": m.get("card"), "角色": m.get("role")}
        for m in members[:30]
    ]
    return _j({"总数": len(members), "成员（最多30）": brief})


@tool(
    "get_member_info",
    "获取指定成员的详细信息",
    {"type": "object", "properties": {"user_id": {"type": "integer", "description": "成员 QQ"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "all",
)
async def _t_member_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not group_id or not uid:
        return "错误：需要群号与用户 QQ。"
    m = await bot.call_api("get_group_member_info", group_id=int(group_id), user_id=int(uid))
    return _j({"QQ": m["user_id"], "昵称": m.get("nickname"), "群名片": m.get("card"), "角色": m.get("role"), "入群时间戳": m.get("join_time"), "等级": m.get("level")})


@tool(
    "get_active_stats",
    "查询群活跃统计（消息量、参与人数、发言Top5）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}, "day": {"type": "string", "description": "YYYY-MM-DD，默认今天"}}, "required": []},
    "all",
)
async def _t_stats(bot, event, args) -> str:
    from src.plugins.ai import _usage_payload  # noqa: F401  （保持模块一致）
    store = get_store()
    day = args.get("day") or datetime.now().astimezone().strftime("%Y-%m-%d")
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if group_id:
        total, users = await store.get_group_day_stat(int(group_id), day)
        top = await store.get_top_users(int(group_id), day)
        return _j({"日期": day, "群号": int(group_id), "消息总数": total, "参与人数": users, "Top5": [{"QQ": u, "条数": c} for u, c in top]})
    overview = await store.get_day_overview(day)
    return _j({"日期": day, "各群": [{"群号": g, "消息数": c} for g, c in overview]})


@tool(
    "mute_member",
    "禁言群成员（分钟）",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "duration_minutes": {"type": "integer", "description": "默认10，上限43200"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_mute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    minutes = int(args.get("duration_minutes", 10))
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=max(60, min(minutes * 60, _ai().MAX_BAN_SECONDS)))
    return f"已禁言 {args['user_id']} {minutes} 分钟。"


@tool(
    "unmute_member",
    "解除成员禁言",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_unmute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=0)
    return f"已解除 {args['user_id']} 的禁言。"


@tool(
    "kick_member",
    "将成员移出群聊",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_kick(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_kick", group_id=int(group_id), user_id=int(args["user_id"]))
    return f"已将 {args['user_id']} 移出群聊。"


@tool(
    "set_whole_ban",
    "开启/关闭全体禁言",
    {"type": "object", "properties": {"enable": {"type": "boolean"}, "group_id": {"type": "integer"}}, "required": ["enable"]},
    "superuser",
)
async def _t_whole_ban(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    enable = bool(args.get("enable", True))
    await bot.call_api("set_group_whole_ban", group_id=int(group_id), enable=enable)
    return "已开启全体禁言。" if enable else "已关闭全体禁言。"


@tool(
    "set_member_card",
    "修改某成员的群名片（机器人需是群管理员；card 留空则清除）",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "card": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_member_card(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not gid or not isinstance(uid, int):
        return "错误：需要 user_id（整数）；群聊中 group_id 默认当前群。"
    ok, msg = await groupextras.set_card(bot, int(gid), uid, str(args.get("card", "")))
    return msg if ok else f"执行失败：{msg}"


@tool(
    "set_member_title",
    "给某成员设置专属头衔（最多 6 个字，机器人需是群主；title 留空则清除）",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "title": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
    "superuser",
)
async def _t_member_title(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not gid or not isinstance(uid, int):
        return "错误：需要 user_id（整数）；群聊中 group_id 默认当前群。"
    ok, msg = await groupextras.set_title(bot, int(gid), uid, str(args.get("title", "")))
    return msg if ok else f"执行失败：{msg}"


@tool(
    "get_group_honor",
    "查看本群（或指定群）的龙王、群聊之火等荣誉成员（用户问“谁是龙王/群里谁最活跃有荣誉”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_group_honor(bot, event, args) -> str:
    from src.plugins import groupextras
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await groupextras.honor_text(bot, int(gid))


@tool(
    "send_like",
    "给提问的人点赞（QQ 资料卡点赞，每人每天一次；用户说“给我点个赞/赞我”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_send_like(bot, event, args) -> str:
    from src.plugins import interact
    ok, msg = await interact.give_like(bot, event.user_id)
    return msg if ok else f"没点成：{msg}"


@tool(
    "poke_member",
    "戳一戳群成员（用户说“戳戳他/戳我一下”时使用；user_id 不填则戳提问的人；同群 20 秒冷却）",
    {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_poke(bot, event, args) -> str:
    from src.plugins import interact
    gid = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id") or event.user_id
    if not gid:
        return "错误：戳一戳只能在群里用（需要 group_id）。"
    ok, msg = await interact.poke(bot, int(gid), int(uid))
    return msg if ok else f"没戳成：{msg}"


@tool(
    "list_group_files",
    "查看群文件（可按文件名关键词搜索，结果少时附下载链接；用户问“群里有没有××文件/群文件有什么”时使用）",
    {"type": "object", "properties": {"keyword": {"type": "string", "description": "文件名关键词，留空列出最近文件"}, "group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_group_files(bot, event, args) -> str:
    from src.plugins import interact
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await interact.files_text(bot, int(gid), str(args.get("keyword", "")))


@tool(
    "recall_message",
    "撤回一条消息（回复目标消息后提出，或提供 message_id）",
    {"type": "object", "properties": {"message_id": {"type": "integer", "description": "可选；不填则撤回当前引用的消息"}}, "required": []},
    "superuser",
)
async def _t_recall(bot, event, args) -> str:
    message_id = args.get("message_id")
    if not message_id and getattr(event, "reply", None) is not None:
        message_id = event.reply.message_id
    if not message_id:
        return "错误：需要 message_id，或回复（引用）目标消息后再让我撤回。"
    await bot.call_api("delete_msg", message_id=int(message_id))
    return f"已撤回消息 {message_id}。"


@tool(
    "set_essence_message",
    "把一条群消息设为精华（用户引用那条消息说“设为精华”，或提供 message_id；机器人需要是群管理员）",
    {"type": "object", "properties": {"message_id": {"type": "integer", "description": "消息 ID，缺省取被引用的消息"}}, "required": []},
    "superuser",
)
async def _t_essence_set(bot, event, args) -> str:
    from src.plugins import essence
    mid = args.get("message_id")
    if not mid and getattr(event, "reply", None) is not None:
        mid = event.reply.message_id
    if not mid:
        return "错误：需要 message_id，或引用要设为精华的消息后再让我设置。"
    ok, msg = await essence.set_essence(bot, int(mid))
    return msg if ok else f"执行失败：{msg}"


@tool(
    "delete_essence_message",
    "取消一条群精华消息（引用该消息或提供 message_id）",
    {"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": []},
    "superuser",
)
async def _t_essence_delete(bot, event, args) -> str:
    from src.plugins import essence
    mid = args.get("message_id")
    if not mid and getattr(event, "reply", None) is not None:
        mid = event.reply.message_id
    if not mid:
        return "错误：需要 message_id，或引用精华消息后再让我取消。"
    ok, msg = await essence.delete_essence(bot, int(mid))
    return msg if ok else f"执行失败：{msg}"


@tool(
    "list_essence_messages",
    "查看本群（或指定群）的精华消息列表",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_essence_list(bot, event, args) -> str:
    from src.plugins import essence
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要 group_id（群聊中默认当前群）。"
    return await essence.list_essence(bot, int(gid))


@tool(
    "publish_group_notice",
    "发布群公告",
    {"type": "object", "properties": {"content": {"type": "string"}, "group_id": {"type": "integer"}}, "required": ["content"]},
    "superuser",
)
async def _t_notice_publish(bot, event, args) -> str:
    content = str(args.get("content", "")).strip()
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not content:
        return "错误：需要 content（公告内容）。"
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    try:
        await bot.call_api("_send_group_notice", group_id=int(group_id), content=content)
    except Exception:
        await bot.call_api("send_group_notice", group_id=int(group_id), content=content)
    return "群公告已发布。"


@tool(
    "get_group_notices",
    "查看当前群公告列表",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "superuser",
)
async def _t_notice_list(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    notices = await bot.call_api("get_group_notice", group_id=int(group_id))
    items = notices if isinstance(notices, list) else notices.get("notices", [])
    return _j({"公告": [{"内容": str(n.get("content", ""))[:100], "发布时间": n.get("publish_time", "")} for n in items[:5]]})


@tool(
    "send_group_message",
    "让机器人在指定群发送一条普通群消息（仅限白名单群；与「群公告」不同；用户说“在群里说一句/帮我发条消息”时使用）",
    {"type": "object", "properties": {"group_id": {"type": "integer", "description": "目标群号，群聊中默认当前群；私聊时必须指定"}, "text": {"type": "string", "description": "要发送的文本内容"}}, "required": ["text"]},
    "superuser",
)
async def _t_group_send(bot, event, args) -> str:
    """超管让机器人在指定群发一条普通消息（仅限白名单群，避免误发到不受控的群）。"""
    from src.permission import merged_whitelist

    text = str(args.get("text", "")).strip()
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not text:
        return "错误：需要 text（要发送的内容）。"
    if not group_id:
        return "错误：需要 group_id（目标群号）；私聊时必须指定，群聊中默认当前群。"
    group_id = int(group_id)
    if group_id not in merged_whitelist():
        return f"错误：群 {group_id} 不在白名单内，为安全起见不向其发送消息。"
    if len(text) > 500:
        return "错误：内容过长（上限 500 字）。"
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=text)
    except Exception as e:
        return f"发送失败：{e}（常见原因：机器人不在该群、被禁言或非管理员）"
    return f"已发送到群 {group_id}：{text[:60]}"


@tool(
    "get_pending_join_requests",
    "查看待审批的入群申请列表（序号、QQ、回答）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_pending_requests(bot, event, args) -> str:
    from src.plugins import request as req
    if not req._PENDING:
        return "当前没有待审批的入群申请。注意：只有机器人运行期间转人工的申请才会进入此列表；QQ 客户端里直接收到的申请需要在 QQ 群管理设置中处理。"
    return _j({"待审批": [
        {"序号": seq, "群号": r["group_id"], "QQ": r["user_id"], "回答": r["comment"] or "（无）"}
        for seq, r in sorted(req._PENDING.items())
    ]})


@tool(
    "approve_join_request",
    "通过入群申请",
    {"type": "object", "properties": {"seq": {"type": "integer", "description": "申请序号"}}, "required": ["seq"]},
    "superuser",
)
async def _t_approve_request(bot, event, args) -> str:
    from src.plugins import request as req
    try:
        seq = int(args["seq"])
    except (KeyError, TypeError, ValueError):
        return "错误：需要 seq（申请序号，可先用查看工具获取）。"
    r = req._PENDING.get(seq)
    if not r:
        return f"没有找到申请 #{seq}（可能已处理、过期或重启丢失）。"
    await bot.call_api("set_group_add_request", flag=r["flag"],
                       sub_type=r["sub_type"], approve=True, reason="")
    req._PENDING.pop(seq)
    return f"已通过申请 #{seq}（QQ {r['user_id']}）。"


@tool(
    "reject_join_request",
    "拒绝入群申请",
    {"type": "object", "properties": {"seq": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["seq"]},
    "superuser",
)
async def _t_reject_request(bot, event, args) -> str:
    from src.plugins import request as req
    try:
        seq = int(args["seq"])
    except (KeyError, TypeError, ValueError):
        return "错误：需要 seq（申请序号）。"
    reason = str(args.get("reason", "不符合入群要求"))
    r = req._PENDING.get(seq)
    if not r:
        return f"没有找到申请 #{seq}（可能已处理、过期或重启丢失）。"
    await bot.call_api("set_group_add_request", flag=r["flag"],
                       sub_type=r["sub_type"], approve=False, reason=reason)
    req._PENDING.pop(seq)
    return f"已拒绝申请 #{seq}（QQ {r['user_id']}），理由：{reason}"


@tool(
    "list_pending_invites",
    "查看待处理的好友申请和拉群邀请（有人加机器人好友、或把机器人拉进新群时会进入这里）",
    {"type": "object", "properties": {}, "required": []},
    "superuser",
)
async def _t_invites_list(bot, event, args) -> str:
    from src.plugins import invites
    return invites.list_text()


@tool(
    "resolve_invite",
    "通过或拒绝好友申请/拉群邀请（先用 list_pending_invites 获取序号）",
    {"type": "object", "properties": {"seq": {"type": "integer", "description": "序号"}, "approve": {"type": "boolean"}, "reason": {"type": "string", "description": "拒绝理由（可空）"}}, "required": ["seq", "approve"]},
    "superuser",
)
async def _t_invite_resolve(bot, event, args) -> str:
    from src.plugins import invites
    seq, approve = args.get("seq"), args.get("approve")
    if not isinstance(seq, int) or not isinstance(approve, bool):
        return "错误：需要 seq（整数，可先用 list_pending_invites 查看）与 approve（布尔值）。"
    ok, msg = await invites.resolve(bot, seq, approve, str(args.get("reason", ""))[:50])
    return msg if ok else f"错误：{msg}"


@tool(
    "set_join_verify",
    "设置新人入群验证（新人入群后须回答算术题；超时或答错 3 次时先在群里 @ 公示并宽限 30 秒，再提醒超管或踢出）：enabled 开关，action 为 notify（仅提醒超管）或 kick（踢出，需机器人是管理员），minutes 时限 1–60；不传参数则只查看当前设置",
    {"type": "object", "properties": {"enabled": {"type": "boolean"}, "action": {"type": "string", "enum": ["notify", "kick"]}, "minutes": {"type": "integer"}}, "required": []},
    "superuser",
)
async def _t_join_verify(bot, event, args) -> str:
    from src.plugins import joinverify
    return await joinverify.set_config(
        enabled=args.get("enabled"), action=args.get("action"), minutes=args.get("minutes"))
