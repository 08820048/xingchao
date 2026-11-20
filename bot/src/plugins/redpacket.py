"""红包提醒：白名单群里出现 QQ 红包时私聊通知超管。**只提醒，不领取**。

背景：NapCat 原生不会转换 QQ 的 walletElement（红包），红包会表现为一条
message=[] / raw_message="" 的空消息；scripts/napcat-wallet-plugin/index.mjs（随 compose 挂载）
会在 NapCat 里注册一个只读转换器，把红包原样转成 json 段，于是本模块能拿到字段。
- 识别：消息文本含 "[QQ红包]"，或任一 json 段里出现 walletElement / 红包特征
- 通知：同一群 60 秒内最多通知一次；开关 kv redpacket_notify（默认开）
- 探测日志：空消息段、红包原始字段都会打日志，方便确认 NapCat 到底上报了什么
- 不领取：只做识别与提醒，NapCat 侧不提供领取/抢红包接口
"""

from __future__ import annotations

import json
import time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger

from src.permission import GROUP_WHITELIST
from src.store import get_store

COOLDOWN = 60.0
_MARKERS = ("walletelement", "qq红包", "[红包]", "红包", "redpacket", "red_packet", "luckymoney")
_WALLET_TEXT_KEYS = (
    "title",
    "summary",
    "pcTitle",
    "desc",
    "subTitle",
    "wishing",
    "amount",
    "money",
    "count",
)
_last: dict[int, float] = {}


def looks_like_red_packet(raw_message: str, segments: list[tuple[str, dict]]) -> bool:
    text = (raw_message or "").lower()
    if "[qq红包]" in text or "walletelement" in text:
        return True
    for t, data in segments:
        if t == "text":
            continue
        blob = json.dumps(data, ensure_ascii=False).lower()
        if any(m in blob for m in _MARKERS):
            return True
    return False


def wallet_info(segments: list[tuple[str, dict]]) -> dict[str, str]:
    """从 json 段里提取红包的可见字段；拿不到就返回空 dict。"""
    for seg_type, data in segments:
        if seg_type != "json":
            continue
        raw = data.get("data")
        if not isinstance(raw, str):
            try:
                raw = json.dumps(raw, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
        if "wallet" not in raw.lower():
            continue
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            continue
        wallet = obj.get("walletElement") or obj.get("wallet") or obj
        if not isinstance(wallet, dict):
            continue
        # NapCat 实际结构：标题在 walletElement.receiver.title，
        # subTitle/notice 也在 receiver 里；sender 通常为空。
        sources = [wallet]
        for key in ("receiver", "sender"):
            nested = wallet.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
        info: dict[str, str] = {}
        for source in sources:
            for key in _WALLET_TEXT_KEYS:
                value = source.get(key)
                if key in info:
                    continue
                if isinstance(value, (str, int, float)) and str(value).strip():
                    info[key] = str(value).strip()
        if info:
            return info
    return {}


def _wallet_tip(info: dict[str, str]) -> str:
    for key in ("title", "summary", "pcTitle", "desc", "subTitle", "wishing", "amount"):
        if info.get(key):
            return f"（{info[key]}）"
    return ""


def _raw_wallet_preview(segments: list[tuple[str, dict]], limit: int = 600) -> str:
    for seg_type, data in segments:
        if seg_type != "json":
            continue
        raw = data.get("data")
        if isinstance(raw, str) and "wallet" in raw.lower():
            return raw[:limit]
    return ""


rp_matcher = on_message(rule=GROUP_WHITELIST, priority=9, block=False)


@rp_matcher.handle()
async def handle_red_packet(bot: Bot, event: GroupMessageEvent) -> None:
    segs = [(s.type, dict(s.data)) for s in event.message]

    # NapCat 原生会丢掉红包元素，只留下一条空消息。这里记下来，
    # 方便和红包插件日志对照；位置、名片等未支持类型也会走到这里。
    if not segs:
        logger.info(
            "收到空消息段（可能是红包/位置/名片等 NapCat 未映射类型）："
            f"group={event.group_id} user={event.user_id} message_id={event.message_id}"
        )
        return

    if not looks_like_red_packet(event.raw_message, segs):
        return

    info = wallet_info(segs)
    raw = _raw_wallet_preview(segs)
    logger.info(
        f"检测到疑似红包：group={event.group_id} user={event.user_id} "
        f"消息段类型={[t for t, _ in segs]} 字段={info}"
    )
    if raw:
        logger.info(f"红包原始消息段：{raw}")

    if (await get_store().get_kv("redpacket_notify")) == "false":
        return
    now = time.monotonic()
    last = _last.get(event.group_id)
    if last is not None and now - last < COOLDOWN:
        return
    _last[event.group_id] = now
    from src.plugins.appeals import notify_superusers

    sender = event.sender.card or event.sender.nickname or str(event.user_id)
    await notify_superusers(
        bot,
        f"🧧 群 {event.group_id} 里 {sender}（{event.user_id}）发了一个红包"
        f"{_wallet_tip(info)}，想要的话快去看看（我不会自动领取）",
    )
