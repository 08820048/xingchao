"""语音消息：发送语音（TTS）+ 接收语音识别（ASR）。

- 发送：`/说 <文字>`（别名 /tts /语音）或 AI 工具 send_voice；用 edge-tts 合成 mp3，
  以 base64 record 段发出（NapCat 自行转码 silk），无需 API Key
- 接收：对机器人说的语音（私聊超管 / 群里回复机器人消息）→ 调 OpenAI 兼容
  /audio/transcriptions 转文字 → 交给 AI 对话回复；`/转文字` 回复一条语音可单独识别
- 默认语音回复：AI 的回复默认以语音发出（按群/私聊独立开关，超管 `/语音回复 开|关`）；
  超过 300 字、含 @ 占位符或合成失败时自动退回文字
- 配置（kv）：voice_name（默认 zh-CN-XiaoxiaoNeural）、voice_asr_model（默认 whisper-1）
- 限制：合成文本 ≤ MAX_TTS_CHARS；每用户冷却 COOLDOWN 秒；语音 ≤ MAX_AUDIO_BYTES
"""

from __future__ import annotations

import asyncio
import base64
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
from src.permission import BASIC, SUPERUSER
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
    audio = await synthesize(text)
    if audio is None:
        return False
    try:
        await bot.send(event, MessageSegment.record("base64://" + base64.b64encode(audio).decode()))
    except Exception:
        logger.exception("语音回复发送失败，退回文字")
        return False
    return True


# ---------------------------------------------------------------- TTS

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
    audio = await synthesize(text)
    if audio is None:
        return "语音合成失败（服务暂不可用），请稍后再试或改用文字回复。"
    _last_tts[event.user_id] = now
    try:
        await bot.send(event, MessageSegment.record("base64://" + base64.b64encode(audio).decode()))
    except Exception as e:
        logger.exception("语音发送失败")
        return f"语音发送失败：{e}（常见原因：机器人被禁言或群禁止发语音）"
    _voiced.add(event.message_id)
    if len(_voiced) > 200:
        _voiced.clear()
    return f"已发送语音：{text[:60]}"


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

    client = await get_client()
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
