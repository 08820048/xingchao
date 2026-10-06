"""图片 AI 视觉审查 + 二维码引流风控：识别白名单群里的图片内容，命中翻墙/VPN、
色情、血腥暴力、API/AI 中转站广告时自动撤回并对发送者禁言（默认 15 分钟），同时私聊通知超管。

- 白名单群；跳过机器人自己与指令消息
- 正常图片：完全静默（不回复描述），只有命中违规才撤回/禁言
- 违规图片：撤回 + 禁言 + 通知超管；发送者为群主/管理员/超管时只通知不处罚，避免误伤
- 二维码引流风控（qrcode_guard_enabled，默认开）：本地解码二维码，命中微信群/微信个人号/
  企业微信/QQ 群等引流码时撤回 + 禁言（qrcode_mute_minutes，默认 15 分钟）+ 通知超管；
  纯本地识别，不消耗 AI 额度，AI 不可用时依然生效；命中类型可在面板配置
- 模型：ai_vision_model（需视觉模型，OpenAI 兼容 image_url 格式；/ai vision <模型> 可改）
- 开关 vision_guard_enabled（面板模块开关 / /plugin vision on|off）
- 限额：每用户冷却 vision_cooldown 秒；每群每日识别 vision_daily_limit 次；每条消息只取前 2 张
- 判定失败（模型不可用/返回不可解析）时按正常放行，避免误伤
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import time
from datetime import datetime

import httpx
import zxingcpp
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from PIL import Image

from src.markdown import md_to_qq
from src.permission import GROUP_WHITELIST
from src.store import get_store

DEFAULTS: dict[str, object] = {
    "vision_guard_enabled": True,
    "vision_mute_minutes": 15,     # 命中后禁言分钟数，0 = 只撤回不禁言
    "vision_notify": True,         # 命中后私聊通知超管
    "vision_cooldown": 10,         # 每用户冷却秒数，防止连发刷 AI
    "vision_daily_limit": 300,     # 每群每日识别上限
    "qrcode_guard_enabled": True,  # 二维码引流风控（本地解码，不耗 AI）
    "qrcode_types": "wechat_group,wechat_personal,wechat_work,qq_group",
    "qrcode_mute_minutes": 15,     # 二维码命中后禁言分钟数，0 = 只撤回不禁言
    "qrcode_notify": True,         # 二维码命中后私聊通知超管
}

VIOLATION_CATEGORIES = {"vpn", "porn", "gore", "api_relay"}
CATEGORY_LABEL = {
    "vpn": "翻墙/VPN",
    "porn": "色情",
    "gore": "血腥暴力",
    "api_relay": "API中转站广告",
    "other": "其他违规",
    "normal": "正常",
}

# 二维码内容分类（正则匹配解码出的文本）
QR_TYPE_LABEL = {
    "wechat_group": "微信群二维码",
    "wechat_personal": "微信个人二维码",
    "wechat_work": "企业微信二维码",
    "qq_group": "QQ群二维码",
    "qq_personal": "QQ名片码",
    "url": "网址二维码",
}
QR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("wechat_group", re.compile(r"weixin\.qq\.com/g/", re.IGNORECASE)),
    ("wechat_personal", re.compile(r"(u\.wechat\.com|weixin\.qq\.com/r/)", re.IGNORECASE)),
    ("wechat_work", re.compile(r"work\.weixin\.qq\.com", re.IGNORECASE)),
    ("qq_group", re.compile(r"qm\.qq\.com", re.IGNORECASE)),
    ("qq_personal", re.compile(r"mqqapi://card", re.IGNORECASE)),
    ("url", re.compile(r"https?://", re.IGNORECASE)),
]

_MAX_IMAGE_BYTES = 3 * 1024 * 1024  # 单张图片下载上限（base64 后约 4MB，适配 glm-4v 限制）
_MAX_IMAGES_PER_MSG = 2

_last_hit: dict[tuple[int, int], float] = {}  # (group_id, user_id) -> monotonic

VISION_PROMPT = (
    "你是群聊图片安全审查助手。请仔细查看这张图片，只输出一个 JSON 对象"
    "（不要 Markdown 代码块、不要任何多余文字）：\n"
    '{"description": "一句话描述图片内容，不超过30字", "category": "normal", "reason": "分类理由，不超过20字"}\n\n'
    "category 只能取以下之一：\n"
    "- normal：正常内容（风景、人物、聊天截图、表情包、正常生活照等）\n"
    "- vpn：翻墙 / VPN / 代理 / 机场 / 科学上网相关（VPN 软件界面、节点订阅、翻墙教程截图等）\n"
    "- porn：色情、裸露、性暗示等成人内容\n"
    "- gore：血腥、暴力、尸体、严重伤害、恐怖画面\n"
    "- api_relay：API / AI 中转站广告或推广（低价代充、转售大模型 API 服务）。典型特征：\n"
    "  价目表或倍率截图（如「0.15 全模型不降智」「纯血 ccmax」「x 元一刀」「官转/号池」）、\n"
    "  「来带量」「老板私聊」等招揽话术、中转站订阅页或推广海报。\n"
    "  注意：正常的 API 文档、代码、报错截图、账单明细、技术讨论不算 api_relay。\n"
    "- other：其他违规但难以归类\n"
    "只输出 JSON。"
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_VPN_KEYS = ("翻墙", "vpn", "代理", "机场", "科学上网", "shadowrocket", "clash", "v2ray", "trojan", "ssr")
_PORN_KEYS = ("色情", "裸露", "性暗示", "成人", "porn", "nsfw", "裸照", "性感")
_GORE_KEYS = ("血腥", "暴力", "尸体", "gore", "恐怖", "断肢", "伤害", "血")
_API_RELAY_KEYS = ("中转", "全模型", "不降智", "纯血", "带量", "代充", "号池", "官转", "低价api", "api中转")


# ---------------------------------------------------------------- 配置

async def _kv(key: str):
    raw = await get_store().get_kv(key)
    if raw is None:
        raw = DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


async def vision_guard_enabled() -> bool:
    return await _kv("vision_guard_enabled") is True


async def vision_config() -> dict[str, object]:
    """图片/二维码审核的全部配置（面板用）。"""
    return {k: await _kv(k) for k in DEFAULTS}


async def _bump_usage(group_id: int) -> None:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"vision_usage_{day}"
    raw = await store.get_kv(key)
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    usage[str(group_id)] = usage.get(str(group_id), 0) + 1
    await store.set_kv(key, json.dumps(usage, ensure_ascii=False))


async def _daily_count(group_id: int) -> int:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"vision_usage_{day}")
    try:
        usage: dict[str, int] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return 0
    return int(usage.get(str(group_id), 0))


# ---------------------------------------------------------------- 图片引用

def _read_image_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read(_MAX_IMAGE_BYTES)


def _path_to_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    data = _read_image_file(path)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def _download_image(url: str) -> tuple[bytes, str] | None:
    """下载图片（限大小与类型），返回 (bytes, content_type)；失败返回 None。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_IMAGE_BYTES:
                        break
                data = b"".join(chunks)
    except Exception:
        logger.debug("图片下载失败", exc_info=True)
        return None
    return (data, ctype) if data else None


async def _download_as_data_uri(url: str) -> str | None:
    """把（可能是内网的）图片 URL 下载到本地转成 data URI，

    避免把 NapCat 的内网地址直接交给模型服务商导致拉取失败。
    """
    got = await _download_image(url)
    if got is None:
        return None
    data, ctype = got
    return f"data:{ctype};base64,{base64.b64encode(data).decode()}"


async def _image_bytes(seg) -> bytes | None:
    """取图片原始字节（本地文件或 http），供本地二维码解码用。"""
    url = str(seg.data.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        got = await _download_image(url)
        return got[0] if got else None
    file = str(seg.data.get("file") or "").strip()
    path = file[7:] if file.startswith("file://") else file
    if path and await asyncio.to_thread(os.path.isfile, path):
        try:
            return await asyncio.to_thread(_read_image_file, path)
        except Exception:
            logger.debug("读取图片文件失败", exc_info=True)
    return None


async def _image_ref(seg) -> str | None:
    """优先把 http url 下载为 data URI；否则尝试本地文件转 data URI。"""
    url = str(seg.data.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        data_uri = await _download_as_data_uri(url)
        if data_uri:
            return data_uri
        return url  # 下载失败时退回 URL（部分服务商可自行拉取公网图）
    file = str(seg.data.get("file") or "").strip()
    path = file[7:] if file.startswith("file://") else file
    if path and await asyncio.to_thread(os.path.isfile, path):
        try:
            return await asyncio.to_thread(_path_to_data_uri, path)
        except Exception:
            logger.debug("图片转 data URI 失败", exc_info=True)
    return url or None


# ---------------------------------------------------------------- 二维码风控

def _decode_qr(data: bytes) -> str | None:
    """解码图片里的二维码，返回内容文本；无二维码/解码失败返回 None。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            result = zxingcpp.read_barcode(img)
    except Exception:
        logger.debug("二维码解码失败", exc_info=True)
        return None
    return str(result.text).strip() if result and result.text else None


def _classify_qr(payload: str) -> str | None:
    """按内容给二维码分类（微信/QQ 等），无匹配返回 None。"""
    for qr_type, pattern in QR_PATTERNS:
        if pattern.search(payload):
            return qr_type
    return None


def _qr_hit(payload: str | None, enabled_types: set[str]) -> str | None:
    """命中返回类型名；未启用该类型或无法分类返回 None。"""
    if not payload:
        return None
    qr_type = _classify_qr(payload)
    if qr_type and qr_type in enabled_types:
        return qr_type
    return None


async def _qrcode_types() -> set[str]:
    return {t.strip() for t in str(await _kv("qrcode_types")).split(",") if t.strip()}


async def _handle_qr_hit(
    bot: Bot, event: GroupMessageEvent, matcher: Matcher, qr_type: str, payload: str
) -> None:
    """二维码引流命中：撤回 + 禁言（可配）+ 通知超管 + 违规记录。"""
    label = QR_TYPE_LABEL.get(qr_type, qr_type)
    matcher.stop_propagation()  # 不再触发 AI / 关键词回复
    privileged = await _is_privileged(bot, event)
    recalled = False
    muted = False
    mute_minutes = int(await _kv("qrcode_mute_minutes"))
    if not privileged:
        try:
            await bot.call_api("delete_msg", message_id=event.message_id)
            recalled = True
        except Exception:
            logger.warning("二维码消息撤回失败（机器人可能不是群管理员）", exc_info=True)
        if mute_minutes > 0:
            try:
                await bot.call_api(
                    "set_group_ban",
                    group_id=event.group_id,
                    user_id=event.user_id,
                    duration=min(mute_minutes * 60, 30 * 24 * 3600),
                )
                muted = True
            except Exception:
                logger.warning("二维码禁言失败（机器人可能不是群管理员）", exc_info=True)
    if recalled or muted:
        action = "recall_mute" if (recalled and muted) else ("mute" if muted else "recall")
        try:
            await get_store().add_punishment(
                group_id=event.group_id,
                user_id=event.user_id,
                word=label,
                action=action,
                mute_minutes=mute_minutes if muted else 0,
                reason=f"二维码内容：{payload[:120]}",
                source="qrcode",
            )
        except Exception:
            logger.exception("写入二维码违规记录失败")
    if await _kv("qrcode_notify"):
        status = "已撤回" if recalled else ("跳过（发送者为管理员）" if privileged else "撤回失败")
        extra = f"，并已禁言 {mute_minutes} 分钟" if muted else ""
        await _notify_superusers(
            bot,
            f"📇 二维码引流告警\n群号: {event.group_id}\nQQ: {event.user_id}\n"
            f"类型: {label}\n内容: {payload[:200]}\n处理: {status}{extra}",
        )


# ---------------------------------------------------------------- 判定

def _parse_result(raw: str) -> tuple[str, str, str]:
    match = _JSON_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group(0))
            desc = str(data.get("description", "")).strip()
            category = str(data.get("category", "normal")).strip().lower()
            reason = str(data.get("reason", "")).strip()
            if category not in CATEGORY_LABEL:
                category = "normal"
            return desc, category, reason
        except json.JSONDecodeError:
            pass
    # 解析失败：退化为关键词判定，任何不确定都按 normal 放行
    low = raw
    if any(k in low.lower() for k in _VPN_KEYS):
        category = "vpn"
    elif any(k in low.lower() for k in _PORN_KEYS):
        category = "porn"
    elif any(k in low.lower() for k in _GORE_KEYS):
        category = "gore"
    elif any(k in low.lower() for k in _API_RELAY_KEYS):
        category = "api_relay"
    else:
        category = "normal"
    return raw.strip()[:60], category, ""


async def _is_privileged(bot: Bot, event: GroupMessageEvent) -> bool:
    """超管 / 群主 / 管理员不自动处罚（只通知），避免误伤管理。"""
    from src.permission import runtime_superuser_ids, superuser_ids

    if event.user_id in (superuser_ids() | runtime_superuser_ids()):
        return True
    try:
        m = await bot.call_api(
            "get_group_member_info",
            group_id=event.group_id, user_id=event.user_id, no_cache=True,
        )
        return m.get("role") in ("owner", "admin")
    except Exception:
        return False


async def _notify_superusers(bot: Bot, text: str) -> None:
    from src.permission import runtime_superuser_ids, superuser_ids

    for uid in sorted(superuser_ids() | runtime_superuser_ids()):
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"图片告警通知超管 {uid} 失败")


# ---------------------------------------------------------------- 处理

vision_matcher = on_message(rule=GROUP_WHITELIST, priority=4, block=False)


@vision_matcher.handle()
async def handle_vision(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id):
        return
    images = [seg for seg in event.message if seg.type == "image"][:_MAX_IMAGES_PER_MSG]
    if not images:
        return
    vision_on = await vision_guard_enabled()
    qr_on = bool(await _kv("qrcode_guard_enabled"))
    if not vision_on and not qr_on:
        return

    ai_on = False
    if vision_on:
        from src.plugins import ai as ai_plugin

        ai_on = await ai_plugin.is_ai_enabled()
    if not qr_on and not ai_on:
        return

    # 每用户冷却（两条链路共用，防连发刷）
    now = time.monotonic()
    key = (event.group_id, event.user_id)
    if now - _last_hit.get(key, 0.0) < int(await _kv("vision_cooldown")):
        return
    # 每群每日上限
    if await _daily_count(event.group_id) >= int(await _kv("vision_daily_limit")):
        return
    _last_hit[key] = now

    # 1) 二维码引流风控：本地解码，不依赖 AI
    if qr_on:
        data = await _image_bytes(images[0])
        if data:
            payload = await asyncio.to_thread(_decode_qr, data)
            qr_type = _qr_hit(payload, await _qrcode_types())
            if qr_type:
                await _handle_qr_hit(bot, event, matcher, qr_type, payload or "")
                return

    # 2) AI 图片审核
    if not (vision_on and ai_on):
        return

    ref = await _image_ref(images[0])
    if not ref:
        return
    raw = await ai_plugin.generate_vision(ref, VISION_PROMPT)
    if not raw:
        return
    await _bump_usage(event.group_id)

    desc, category, reason = _parse_result(raw)
    desc = md_to_qq(desc).strip().replace("\n", " ")

    if category in VIOLATION_CATEGORIES:
        matcher.stop_propagation()  # 不再触发 AI / 关键词回复
        privileged = await _is_privileged(bot, event)
        recalled = False
        muted = False
        mute_minutes = int(await _kv("vision_mute_minutes"))
        if not privileged:
            try:
                await bot.call_api("delete_msg", message_id=event.message_id)
                recalled = True
            except Exception:
                logger.warning("违规图片撤回失败（机器人可能不是群管理员）", exc_info=True)
            if mute_minutes > 0:
                try:
                    await bot.call_api(
                        "set_group_ban",
                        group_id=event.group_id,
                        user_id=event.user_id,
                        duration=min(mute_minutes * 60, 30 * 24 * 3600),
                    )
                    muted = True
                except Exception:
                    logger.warning("违规图片禁言失败（机器人可能不是群管理员）", exc_info=True)
        if recalled or muted:
            action = "recall_mute" if (recalled and muted) else ("mute" if muted else "recall")
            try:
                await get_store().add_punishment(
                    group_id=event.group_id,
                    user_id=event.user_id,
                    word=CATEGORY_LABEL.get(category, category),
                    action=action,
                    mute_minutes=mute_minutes if muted else 0,
                    reason=reason,
                    source="vision",
                )
            except Exception:
                logger.exception("写入图片违规记录失败")
        if await _kv("vision_notify"):
            status = "已撤回" if recalled else ("跳过（发送者为管理员）" if privileged else "撤回失败")
            extra = f"，并已禁言 {mute_minutes} 分钟" if muted else ""
            await _notify_superusers(
                bot,
                f"🖼️ 图片违规告警\n群号: {event.group_id}\nQQ: {event.user_id}\n"
                f"类型: {CATEGORY_LABEL.get(category, category)}\n"
                f"描述: {desc}\n理由: {reason}\n处理: {status}{extra}",
            )
        return

    # 正常图片：静默放行，不做任何回复/提示
