"""机器人账号自己的 QQ 资料感知：会员（SVIP / 年费 / 等级）、QQ 等级、注册时间等。

- 数据来自 NapCat get_stranger_info（查自己）；连上 NapCat 时取一次，之后每小时刷新
- 结果缓存在内存，同时写 kv bot_self_info（重启后、NapCat 暂时取不到时仍有值）
- AI 每次对话的场景里带一行摘要（ai._build_scene），也可以用 AI 工具 get_self_info 查完整信息
- 会员状态发生变化（开通 / 到期）时记一条日志
- 局限：NapCat 只给出 SVIP 标记、年费标记和 VIP 等级，看不到具体到期日，也区分不出
  QQ 里其他会员种类（如绿钻、大会员）；字段取不到时按「未知」处理，不当作「不是会员」
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from src.background import periodic
from src.store import get_store

REFRESH_SECONDS = 3600
KV_KEY = "bot_self_info"

_info: dict[str, Any] = {}


def parse_info(raw: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """把 get_stranger_info 的返回整理成我们要用的几个字段。"""
    out: dict[str, Any] = {
        "uin": raw.get("user_id"),
        "nickname": raw.get("nickname") or "",
        "signature": raw.get("long_nick") or "",
        "qq_level": raw.get("qqLevel"),
        "reg_time": raw.get("reg_time") or None,
        "updated": int(now if now is not None else time.time()),
    }
    # 字段缺失（NapCat 版本不给）时存 None，表示「不知道」，和 False（明确不是会员）区分开
    for key, src in (("svip", "is_vip"), ("year_vip", "is_years_vip")):
        out[key] = bool(raw[src]) if src in raw and raw[src] is not None else None
    level = raw.get("vip_level")
    out["vip_level"] = int(level) if isinstance(level, (int, float)) else None
    return out


def vip_text(info: dict[str, Any]) -> str:
    """会员状态的一句话描述。"""
    svip, year, level = info.get("svip"), info.get("year_vip"), info.get("vip_level")
    if svip is None and year is None and level is None:
        return "会员状态未知"
    if svip:
        text = "SVIP" + ("（年费）" if year else "")
    elif year:
        text = "年费会员"
    else:
        text = "不是会员"
    if level:
        text += f"，VIP{level}"
    return text


def summary(info: dict[str, Any] | None = None) -> str:
    """给 AI 场景用的一行摘要；还没取到资料时返回空串。"""
    info = _info if info is None else info
    if not info:
        return ""
    parts = [f"会员：{vip_text(info)}"]
    if info.get("qq_level"):
        parts.append(f"QQ 等级 {info['qq_level']}")
    if info.get("reg_time"):
        try:
            parts.append(f"{datetime.fromtimestamp(int(info['reg_time'])).year} 年注册")
        except (OverflowError, OSError, ValueError):
            pass
    return "你自己的 QQ 账号：" + "，".join(parts) + "（被问到自己的会员 / 等级时据此如实回答，不要编造到期时间）"


def current() -> dict[str, Any]:
    return dict(_info)


async def refresh(bot: Bot) -> dict[str, Any]:
    """从 NapCat 取一次自己的资料并更新缓存；失败时保留旧值。"""
    global _info
    try:
        raw = await bot.call_api("get_stranger_info", user_id=int(bot.self_id), no_cache=True)
    except Exception:
        logger.debug("获取机器人账号资料失败", exc_info=True)
        return _info
    new = parse_info(raw or {})
    old = _info
    if old and (old.get("svip"), old.get("year_vip")) != (new.get("svip"), new.get("year_vip")):
        logger.info(f"机器人账号会员状态变化：{vip_text(old)} → {vip_text(new)}")
    _info = new
    try:
        await get_store().set_kv(KV_KEY, json.dumps(new, ensure_ascii=False))
    except Exception:
        logger.debug("保存机器人账号资料失败", exc_info=True)
    return new


async def load_saved() -> None:
    """启动时先读上次保存的值，NapCat 还没连上也有得回答。"""
    global _info
    if _info:
        return
    raw = await get_store().get_kv(KV_KEY)
    if raw:
        try:
            _info = json.loads(raw)
        except ValueError:
            pass


@get_driver().on_bot_connect
async def _on_connect(bot: Bot) -> None:
    await refresh(bot)


@periodic("账号资料刷新", REFRESH_SECONDS)
async def _refresh_loop(bot: Bot) -> None:
    await refresh(bot)


@get_driver().on_startup
async def _on_startup() -> None:
    await load_saved()


def describe() -> str:
    """完整的文字说明，给 AI 工具 get_self_info 用。"""
    info = _info
    if not info:
        return "暂时取不到自己的账号资料。"
    lines = [f"会员：{vip_text(info)}"]
    if info.get("qq_level"):
        lines.append(f"QQ 等级：{info['qq_level']}")
    if info.get("reg_time"):
        lines.append("注册时间：" + datetime.fromtimestamp(int(info["reg_time"])).strftime("%Y-%m-%d"))
    if info.get("nickname"):
        lines.append(f"昵称：{info['nickname']}")
    if info.get("signature"):
        lines.append(f"个性签名：{info['signature']}")
    lines.append("（会员到期时间和其他类型的会员，这个接口查不到）")
    return "\n".join(lines)
