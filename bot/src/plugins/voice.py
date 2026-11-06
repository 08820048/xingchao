"""语音消息：发送语音（TTS）+ 接收语音识别（ASR）。

- 发送：`/说 <文字>`（别名 /tts /语音）或 AI 工具 send_voice；群聊优先用 NapCat 的
  QQ AI 语音（send_group_ai_record），失败/私聊退回 edge-tts 合成 mp3，以 base64 record 段发出
- 接收：对机器人说的语音（私聊超管 / 群里回复机器人消息）→ 调 OpenAI 兼容
  /audio/transcriptions 转文字 → 交给 AI 对话回复；`/转文字` 回复一条语音可单独识别
- 默认语音回复：AI 的回复默认以语音发出（按群/私聊独立开关，超管 `/语音回复 开|关`）；
  超过 300 字、含 @ 占位符或合成失败时自动退回文字
- 换音色（超管）：/音色 查看当前与可选列表；/音色 <序号|名称|ID> 切换 QQ AI 语音角色并试听；
  /音色 edge <名称> 切换 edge-tts 音色（私聊 / 兜底用）；/音色 试听；/音色 重置
- 配置（kv）：voice_ai_character（QQ AI 语音角色 ID，空=自动取第一个）、voice_ai_record（false 关闭 AI 语音）、voice_name（默认 zh-CN-XiaoxiaoNeural）、voice_asr_model（默认 whisper-1）
- 限制：合成文本 ≤ MAX_TTS_CHARS；每用户冷却 COOLDOWN 秒；语音 ≤ MAX_AUDIO_BYTES
"""

from __future__ import annotations

import asyncio
import base64
import re
import time

import httpx
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.markdown import md_to_qq
from src.permission import BASIC, SUPERUSER, merged_whitelist
from src.store import get_store

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_ASR_MODEL = "whisper-1"
MAX_TTS_CHARS = 300
MAX_AUDIO_BYTES = 10 * 1024 * 1024
COOLDOWN = 15

_last_tts: dict[int, float] = {}
_voiced: set[int] = set()  # 本条消息已由 send_voice 工具发过语音，避免文字回复再被转成第二条语音


def _scope(event: MessageEvent) -> str:
    gid = getattr(event, "group_id", None)
    return f"voice_reply_g{gid}" if gid else f"voice_reply_u{event.user_id}"


async def voice_reply_enabled(event: MessageEvent) -> bool:
    """AI 回复是否默认用语音：按群/私聊独立开关，未设置 = 开启。"""
    return await get_store().get_kv(_scope(event)) != "false"


async def set_voice_reply(event: MessageEvent, enabled: bool) -> None:
    await get_store().set_kv(_scope(event), "true" if enabled else "false")


async def deliver_reply(bot: Bot, event: MessageEvent, reply: str) -> bool:
    """语音回复模式下把 AI 回复发成语音；返回 True 表示已发出（调用方不用再发文字）。

    不适用（开关关闭 / 过长 / 含 @ 占位符 / 合成或发送失败）时返回 False，调用方照常发文字。
    """
    if event.message_id in _voiced or "{dev_at}" in reply:
        return False
    if not await voice_reply_enabled(event):
        return False
    text = md_to_qq(reply).strip()
    if not text or len(text) > MAX_TTS_CHARS:
        return False
    err = await emit_voice(bot, event, text)
    if err:
        logger.warning(f"语音回复失败，退回文字：{err}")
    return err is None


# ---------------------------------------------------------------- TTS

_ai_character: str | None = None  # 缓存的 QQ AI 语音角色 ID


async def _pick_ai_character(bot: Bot, group_id: int) -> str | None:
    """QQ AI 语音角色：kv voice_ai_character 优先，否则取 get_ai_characters 的第一个。"""
    global _ai_character
    configured = await get_store().get_kv("voice_ai_character")
    if configured:
        return str(configured)
    if _ai_character:
        return _ai_character
    res = await bot.call_api("get_ai_characters", group_id=group_id, chat_type=1)
    groups = res if isinstance(res, list) else (res or {}).get("data", [])
    for grp in groups:
        for ch in grp.get("characters", []):
            if ch.get("character_id"):
                _ai_character = str(ch["character_id"])
                return _ai_character
    return None


async def emit_voice(bot: Bot, event: MessageEvent, text: str) -> str | None:
    """发送语音，成功返回 None，失败返回中文错误说明。

    群聊优先用 NapCat 的 QQ 官方 AI 语音（send_group_ai_record，音色自然、不依赖外网）；
    私聊或 AI 语音不可用（旧版 NapCat / 接口报错）时退回 edge-tts + record 段。
    """
    group_id = getattr(event, "group_id", None)
    if group_id and await get_store().get_kv("voice_ai_record") != "false":
        try:
            character = await _pick_ai_character(bot, group_id)
            if character:
                await bot.call_api(
                    "send_group_ai_record", group_id=group_id, character=character, text=text
                )
                return None
            logger.warning("get_ai_characters 未返回可用角色，退回 edge-tts")
        except Exception:
            logger.warning("QQ AI 语音失败，退回 edge-tts", exc_info=True)
    audio = await synthesize(text)
    if audio is None:
        return "语音合成失败（服务暂不可用），请稍后再试或改用文字回复。"
    try:
        await bot.send(event, MessageSegment.record("base64://" + base64.b64encode(audio).decode()))
    except Exception as e:
        logger.exception("语音发送失败")
        return f"语音发送失败：{e}（常见原因：机器人被禁言或群禁止发语音）"
    return None

async def synthesize(text: str) -> bytes | None:
    """文本 → mp3 字节；失败返回 None。"""
    try:
        import edge_tts
    except ImportError:
        logger.error("未安装 edge-tts，无法合成语音")
        return None
    voice = await get_store().get_kv("voice_name") or DEFAULT_VOICE
    chunks = bytearray()
    try:
        async with asyncio.timeout(30):
            async for part in edge_tts.Communicate(text, str(voice)).stream():
                if part["type"] == "audio":
                    chunks.extend(part["data"])
    except Exception:
        logger.exception("edge-tts 合成失败")
        return None
    return bytes(chunks) or None


async def send_voice(bot: Bot, event: MessageEvent, text: str) -> str:
    """合成并发送语音到事件所在会话；返回给调用方（AI/指令）的中文结果。"""
    text = md_to_qq(text).strip()
    if not text:
        return "错误：需要 text（要说的内容）。"
    if len(text) > MAX_TTS_CHARS:
        return f"错误：内容过长（上限 {MAX_TTS_CHARS} 字），请精简后再发语音。"
    now = time.monotonic()
    wait = COOLDOWN - (now - _last_tts.get(event.user_id, 0))
    if wait > 0:
        return f"发语音太频繁了，请 {int(wait) + 1} 秒后再试。"
    err = await emit_voice(bot, event, text)
    if err:
        return err
    _last_tts[event.user_id] = now
    _voiced.add(event.message_id)
    if len(_voiced) > 200:
        _voiced.clear()
    return f"已发送语音：{text[:60]}"


# ---------------------------------------------------------------- 换音色

EDGE_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓·女·温暖自然（默认）"),
    ("zh-CN-XiaoyiNeural", "晓伊·女·活泼"),
    ("zh-CN-YunxiNeural", "云希·男·阳光少年"),
    ("zh-CN-YunjianNeural", "云健·男·激昂沉稳"),
    ("zh-CN-YunyangNeural", "云扬·男·新闻播报"),
    ("zh-CN-YunxiaNeural", "云夏·男·可爱少年"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北·女·东北话"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮·女·陕西话"),
    ("zh-HK-HiuMaanNeural", "曉曼·女·粤语"),
]
_EDGE_NAME_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}(-[a-z]+)?-[A-Za-z]+Neural$")
SAMPLE_TEXT = "你好呀，我是星潮，这是我现在的声音，你觉得好听吗？"


async def list_ai_characters(bot: Bot, group_id: int) -> list[tuple[str, str]]:
    """QQ AI 语音可用角色 [(character_id, 名称)]，去重保序。"""
    res = await bot.call_api("get_ai_characters", group_id=group_id, chat_type=1)
    groups = res if isinstance(res, list) else (res or {}).get("data", [])
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for grp in groups:
        for ch in grp.get("characters", []):
            cid = str(ch.get("character_id") or "")
            if cid and cid not in seen:
                seen.add(cid)
                out.append((cid, str(ch.get("character_name") or cid)))
    return out


def pick_character(arg: str, chars: list[tuple[str, str]]) -> tuple[str, str] | None:
    """按序号（从 1 起）/ ID / 名称（精确优先，其次包含）匹配角色。"""
    arg = arg.strip()
    if arg.isdigit() and 1 <= int(arg) <= len(chars):
        return chars[int(arg) - 1]
    for cid, name in chars:
        if arg == cid or arg == name:
            return cid, name
    hits = [(cid, name) for cid, name in chars if arg and arg in name]
    return hits[0] if len(hits) == 1 else None


voice_cmd = on_command("音色", aliases={"voice"}, rule=SUPERUSER, priority=5, block=True)


def _api_group(event: MessageEvent) -> int | None:
    gid = getattr(event, "group_id", None)
    if gid:
        return gid
    groups = sorted(merged_whitelist())
    return groups[0] if groups else None


@voice_cmd.handle()
async def handle_voice_cmd(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    global _ai_character
    store = get_store()
    arg = args.extract_plain_text().strip()
    gid = _api_group(event)

    if arg in ("试听", "test"):
        err = await emit_voice(bot, event, SAMPLE_TEXT)
        if err:
            await matcher.finish(err)
        return

    if arg in ("重置", "reset"):
        await store.set_kv("voice_ai_character", "")
        await store.set_kv("voice_name", "")
        _ai_character = None
        await matcher.finish("已恢复默认音色（QQ AI 语音取第一个角色，edge-tts 用晓晓）")

    if arg.lower().startswith("edge"):
        name = arg[4:].strip()
        if not name:
            cur = await store.get_kv("voice_name") or DEFAULT_VOICE
            lines = [f"edge-tts 音色（私聊 / AI 语音不可用时使用），当前：{cur}"]
            lines += [f"• {n}  {d}" for n, d in EDGE_VOICES]
            lines.append("切换：/音色 edge <名称>")
            await matcher.finish("\n".join(lines))
        if not _EDGE_NAME_RE.match(name):
            await matcher.finish("音色名称格式不对，例如 zh-CN-YunxiNeural，发 /音色 edge 查看可选项")
        await store.set_kv("voice_name", name)
        await matcher.send(f"已切换 edge-tts 音色为 {name}，试听中……")
        audio = await synthesize(SAMPLE_TEXT)
        if audio is None:
            await matcher.finish("合成失败：该音色可能不存在，请换一个，或 /音色 重置")
        await bot.send(event, MessageSegment.record("base64://" + base64.b64encode(audio).decode()))
        return

    if gid is None:
        await matcher.finish("没有可用的群来查询 QQ AI 语音角色，请先在白名单群里使用此指令。")
    try:
        chars = await list_ai_characters(bot, gid)
    except Exception as e:
        logger.warning("get_ai_characters 失败", exc_info=True)
        await matcher.finish(f"获取 QQ AI 语音角色失败：{e}（NapCat 版本过旧或不支持）。可用 /音色 edge 切换 edge-tts 音色")
    if not chars:
        await matcher.finish("NapCat 没有返回任何 QQ AI 语音角色。可用 /音色 edge 切换 edge-tts 音色")

    current = str(await store.get_kv("voice_ai_character") or _ai_character or chars[0][0])
    if not arg:
        lines = ["QQ AI 语音角色（群聊使用）："]
        for i, (cid, name) in enumerate(chars[:40], 1):
            mark = " ◀ 当前" if cid == current else ""
            lines.append(f"{i}. {name}{mark}")
        if len(chars) > 40:
            lines.append(f"……共 {len(chars)} 个，只显示前 40 个，可直接用名称或 ID 选择")
        lines.append("切换：/音色 <序号或名称>（会自动试听）；/音色 edge 查看私聊音色；/音色 试听；/音色 重置")
        await matcher.finish("\n".join(lines))

    chosen = pick_character(arg, chars)
    if chosen is None:
        await matcher.finish("没找到唯一匹配的角色，请用 /音色 查看列表后按序号选择。")
    cid, name = chosen
    await store.set_kv("voice_ai_character", cid)
    if getattr(event, "group_id", None):
        err = await emit_voice(bot, event, SAMPLE_TEXT)
        if err:
            await matcher.finish(f"已切换为「{name}」，但试听失败：{err}")
        return
    await matcher.finish(f"已切换为「{name}」。在群里发 /音色 试听 可听效果。")


# ---------------------------------------------------------------- ASR

async def _record_bytes(bot: Bot, seg: MessageSegment) -> bytes | None:
    """取语音原始字节：优先 NapCat get_record(base64)，其次 url / 本地文件。"""
    file = str(seg.data.get("file") or "")
    try:
        res = await bot.call_api("get_record", file=file, out_format="mp3")
        b64 = res.get("base64") if isinstance(res, dict) else None
        if b64:
            return base64.b64decode(b64)
        url = (res.get("url") if isinstance(res, dict) else None) or seg.data.get("url")
    except Exception:
        logger.debug("get_record 失败，退回 url", exc_info=True)
        url = seg.data.get("url")
    if url and str(url).startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                r = await client.get(str(url))
                r.raise_for_status()
                return r.content
        except Exception:
            logger.warning("下载语音失败", exc_info=True)
    return None


async def transcribe(bot: Bot, seg: MessageSegment) -> str | None:
    """语音段 → 文字；失败返回 None。"""
    from src.plugins.ai import get_client

    client = await get_client("audio")
    if client is None:
        return None
    data = await _record_bytes(bot, seg)
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    model = await get_store().get_kv("voice_asr_model") or DEFAULT_ASR_MODEL
    try:
        res = await client.audio.transcriptions.create(
            model=str(model), file=("voice.mp3", data, "audio/mpeg")
        )
    except Exception:
        logger.exception("语音识别失败（服务商可能不支持 /audio/transcriptions 或模型名不对）")
        return None
    return (getattr(res, "text", "") or "").strip() or None


def _record_seg(message: Message) -> MessageSegment | None:
    return next((s for s in message if s.type == "record"), None)


# ---------------------------------------------------------------- 指令

say_cmd = on_command("说", aliases={"tts", "语音"}, rule=BASIC, priority=5, block=True)


@say_cmd.handle()
async def handle_say(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    if not text:
        await matcher.finish("用法：/说 <文字>，我会用语音念出来（最多 300 字）")
    result = await send_voice(bot, event, text)
    if not result.startswith("已发送"):
        await matcher.finish(result)


toggle_cmd = on_command("语音回复", rule=SUPERUSER, priority=5, block=True)


@toggle_cmd.handle()
async def handle_toggle(event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip().lower()
    if arg not in {"开", "关", "on", "off"}:
        state = "开启" if await voice_reply_enabled(event) else "关闭"
        await matcher.finish(f"当前会话 AI 语音回复：{state}\n用法：/语音回复 开|关")
    await set_voice_reply(event, arg in {"开", "on"})
    await matcher.finish("已开启 AI 语音回复（过长内容仍用文字）" if arg in {"开", "on"} else "已关闭 AI 语音回复，改回文字")


asr_cmd = on_command("转文字", aliases={"asr"}, rule=BASIC, priority=5, block=True)


@asr_cmd.handle()
async def handle_asr_cmd(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    seg = _record_seg(event.reply.message) if event.reply else None
    if seg is None:
        await matcher.finish("用法：回复一条语音消息并发送 /转文字")
    text = await transcribe(bot, seg)
    await matcher.finish(f"🎙 识别结果：{text}" if text else "没能识别出内容（语音太长、无声或识别服务不可用）")


# ---------------------------------------------------------------- 对话语音

voice_matcher = on_message(rule=BASIC, priority=11, block=False)


@voice_matcher.handle()
async def handle_voice(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """对机器人说的语音（私聊超管 / 回复机器人）：识别后交给 AI 对话。"""
    seg = _record_seg(event.message)
    if seg is None or not event.to_me:
        return
    from nonebot import get_driver

    from src.plugins.ai import chat, is_ai_enabled

    if not await is_ai_enabled():
        return
    if event.message_type == "private" and event.user_id not in {int(u) for u in get_driver().config.superusers}:
        return
    text = await transcribe(bot, seg)
    if not text:
        await matcher.finish("语音没听清，能再说一遍或者打字吗？")
    reply = await chat(event, f"（用户发来一条语音，识别内容如下）{text}", bot=bot, history_user_text=f"[语音] {text}")
    try:
        if reply and await deliver_reply(bot, event, reply):
            return
        msg = MessageSegment.reply(event.message_id) + f"🎙 {text}"
        if reply:
            msg += "\n" + md_to_qq(reply)
        await matcher.send(msg)
    except MatcherException:
        raise
    except Exception:
        logger.exception("语音回复发送失败")
