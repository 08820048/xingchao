"""AI 工具：互动与创作：语音、自拍、表情包、投票、生成图片。"""

from __future__ import annotations

from src.aitools.registry import tool


@tool(
    "send_voice",
    "在当前会话发送一条语音消息，把 text 用语音念出来（用户说“发语音/用语音说/念给我听/语音回复我”时使用；text 是要说的话，口语化、300 字内；发送成功后文字回复尽量简短，不要重复语音内容）",
    {"type": "object", "properties": {"text": {"type": "string", "description": "要用语音说的内容"}}, "required": ["text"]},
    "all",
)
async def _t_send_voice(bot, event, args) -> str:
    from src.plugins.voice import send_voice

    return await send_voice(bot, event, str(args.get("text", "")))


@tool(
    "send_selfie",
    "发送一张星潮本人的照片/自拍到当前会话（用户说“看看你的照片/发张自拍/你长什么样/给我看看你”时使用；发送后文字回复简短自然，不要描述图片细节）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_send_selfie(bot, event, args) -> str:
    from src.plugins.selfie import send_selfie

    return await send_selfie(bot, event)


@tool(
    "create_poll",
    "在群里发起投票（2–8 个选项，默认 30 分钟，到时自动公布结果；用户说“发起个投票：晚上吃啥 火锅 烧烤”时使用）",
    {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "minutes": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["question", "options"]},
    "all",
)
async def _t_poll_create(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：投票只能在群里发起。"
    opts = args.get("options") or []
    ok, msg = polls.create_poll(int(gid), event.user_id, str(args.get("question", "")),
                                [str(o) for o in opts], int(args.get("minutes") or polls.DEFAULT_MINUTES))
    if ok:
        try:
            await bot.call_api("send_group_msg", group_id=int(gid), message=msg)
        except Exception as e:
            return f"投票已创建但发送到群失败：{e}"
        return "投票已发到群里，截止后会自动公布结果。"
    return f"没发起成功：{msg}"


@tool(
    "get_poll_result",
    "查看本群当前投票的票数",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_poll_result(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    return polls.get_result(int(gid)) if gid else "错误：需要在群里查询。"


@tool(
    "end_poll",
    "提前结束本群投票并公布结果（仅发起人或超管有效）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "all",
)
async def _t_poll_end(bot, event, args) -> str:
    from src.plugins import polls
    gid = args.get("group_id") or getattr(event, "group_id", None)
    if not gid:
        return "错误：需要在群里操作。"
    ok, msg = polls.end_poll(int(gid), event.user_id)
    if ok:
        try:
            await bot.call_api("send_group_msg", group_id=int(gid), message=msg)
        except Exception:
            pass
        return "投票已结束，结果已发到群里。"
    return msg


@tool(
    "generate_image",
    "AI 画图并发送到当前会话（用户说“画一张/帮我画”时使用；每人每天有限额；发送后文字回应简短自然）",
    {"type": "object", "properties": {"prompt": {"type": "string", "description": "画面描述"}}, "required": ["prompt"]},
    "all",
)
async def _t_generate_image(bot, event, args) -> str:
    from src.plugins import imagegen
    seg, tip = await imagegen.generate(event.user_id, str(args.get("prompt", "")))
    if seg is None:
        return f"没画成：{tip}"
    try:
        await bot.send(event, seg)
    except Exception as e:
        return f"图片已生成但发送失败：{e}"
    return "图片已发送，请用一句话简短自然地回应（不要描述图片细节）。"


@tool(
    "send_sticker",
    "发一张表情包到当前会话（从机器人收藏的表情包库里按心情挑；聊天气氛合适、想用表情包回应时使用，如 无语/开心/狗头/委屈/吃瓜/加油）",
    {"type": "object", "properties": {"mood": {"type": "string", "description": "想表达的心情或梗，如 无语、开心、狗头"}}, "required": ["mood"]},
    "all",
)
async def _t_send_sticker(bot, event, args) -> str:
    from src.plugins import stickers

    count = await stickers.ensure_library(bot)
    if not count:
        return "表情包库是空的（机器人账号里还没有收藏表情，或还没来得及打标签）。"
    sid = await stickers.pick_by_mood(str(args.get("mood", "")))
    msg = stickers.sticker_message(sid) if sid else None
    if msg is None:
        return "没找到合适的表情包。"
    await bot.send(event, msg)
    return "已发送一张表情包；文字回复保持简短自然，不要描述这张图。"
