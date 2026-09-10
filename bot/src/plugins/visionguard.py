"""图片 AI 视觉审查：识别白名单群里的图片内容，命中翻墙/VPN、色情、血腥暴力时
自动撤回消息并对发送者禁言（默认 15 分钟），同时私聊通知超管。

- 白名单群；跳过机器人自己与指令消息
- 正常图片：完全静默（不回复描述），只有命中违规才撤回/禁言
- 违规图片：撤回 + 禁言 + 通知超管；发送者为群主/管理员/超管时只通知不处罚，避免误伤
- 模型：ai_vision_model（需视觉模型，OpenAI 兼容 image_url 格式；/ai vision <模型> 可改）
- 开关 vision_guard_enabled（面板模块开关 / /plugin vision on|off）
- 限额：每用户冷却 vision_cooldown 秒；每群每日识别 vision_daily_limit 次；每条消息只取前 2 张
- 判定失败（模型不可用/返回不可解析）时按正常放行，避免误伤
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import time
from datetime import datetime

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.markdown import md_to_qq
from src.permission import GROUP_WHITELIST
from src.store import get_store

DEFAULTS: dict[str, object] = {
    "vision_guard_enabled": True,
    "vision_mute_minutes": 15,     # 命中后禁言分钟数，0 = 只撤回不禁言
    "vision_notify": True,         # 命中后私聊通知超管
    "vision_cooldown": 10,         # 每用户冷却秒数，防止连发刷 AI
    "vision_daily_limit": 300,     # 每群每日识别上限
}

VIOLATION_CATEGORIES = {"vpn", "porn", "gore"}
CATEGORY_LABEL = {"vpn": "翻墙/VPN", "porn": "色情", "gore": "血腥暴力", "other": "其他违规", "normal": "正常"}

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
    "- other：其他违规但难以归类\n"
    "只输出 JSON。"
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_VPN_KEYS = ("翻墙", "vpn", "代理", "机场", "科学上网", "shadowrocket", "clash", "v2ray", "trojan", "ssr")
_PORN_KEYS = ("色情", "裸露", "性暗示", "成人", "porn", "nsfw", "裸照", "性感")
_GORE_KEYS = ("血腥", "暴力", "尸体", "gore", "恐怖", "断肢", "伤害", "血")


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

def _path_to_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        data = f.read(_MAX_IMAGE_BYTES)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def _download_as_data_uri(url: str) -> str | None:
    """把（可能是内网的）图片 URL 下载到本地转成 data URI，

    避免把 NapCat 的内网地址直接交给模型服务商导致拉取失败。
    """
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
    if not data:
        return None
    return f"data:{ctype};base64,{base64.b64encode(data).decode()}"


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
    if path and os.path.isfile(path):
        try:
            return await asyncio.to_thread(_path_to_data_uri, path)
        except Exception:
            logger.debug("图片转 data URI 失败", exc_info=True)
    return url or None


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
    if not await vision_guard_enabled():
        return

    from src.plugins import ai as ai_plugin

    if not await ai_plugin.is_ai_enabled():
        return

    # 每用户冷却
    now = time.monotonic()
    key = (event.group_id, event.user_id)
    if now - _last_hit.get(key, 0.0) < int(await _kv("vision_cooldown")):
        return
    # 每群每日上限
    if await _daily_count(event.group_id) >= int(await _kv("vision_daily_limit")):
        return
    _last_hit[key] = now

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
