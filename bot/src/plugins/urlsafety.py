"""链接安全提醒：群里出现被 QQ 安全中心标记为危险的链接时，提醒群友并通知超管。

- 只提醒、不自动撤回/禁言（安全中心有误报，处置交给管理员）
- 每条消息最多检查 2 个链接；每群每天最多 40 次检查；同一链接 6 小时内只查一次
- 接口不支持时自动停用；开关 kv url_safety_enabled（默认开）：/plugin urlsafe on|off
- 被标记为危险的链接，链接解读（linkpreview）也会跳过，不去抓取它
"""

from __future__ import annotations

import re
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.log import logger

from src.permission import GROUP_WHITELIST
from src.plugins import nctools
from src.store import get_store

MAX_URLS = 2
DAILY_LIMIT = 40
_URL_RE = re.compile(r'https?://[^\s<>"\')\]}，。；！？、]+', re.IGNORECASE)
_daily: dict[tuple[int, str], int] = {}


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for u in (x.rstrip(".,;:!?") for x in _URL_RE.findall(text)):
        if u not in seen:
            seen.append(u)
    return seen[:MAX_URLS]


def _take_quota(group_id: int) -> bool:
    day = datetime.now().strftime("%Y-%m-%d")
    for k in [k for k in _daily if k[1] != day]:
        _daily.pop(k, None)
    n = _daily.get((group_id, day), 0)
    if n >= DAILY_LIMIT:
        return False
    _daily[(group_id, day)] = n + 1
    return True


def warning_text(url: str) -> str:
    shown = url if len(url) <= 60 else url[:57] + "…"
    return f"⚠️ 小心：这个链接（{shown}）被 QQ 安全中心标记为有风险，请不要点开，更别输入账号密码。"


urlsafety_matcher = on_message(rule=GROUP_WHITELIST, priority=5, block=False)


@urlsafety_matcher.handle()
async def handle_urlsafety(bot: Bot, event: GroupMessageEvent) -> None:
    if event.user_id == int(bot.self_id):
        return
    urls = extract_urls(event.message.extract_plain_text())
    if not urls or await get_store().get_kv("url_safety_enabled") == "false":
        return
    for url in urls:
        if not nctools.known_dangerous(url) and not _take_quota(event.group_id):
            return
        if await nctools.check_url(bot, url) != "danger":
            continue
        try:
            await bot.call_api(
                "send_group_msg", group_id=event.group_id,
                message=MessageSegment.reply(event.message_id) + warning_text(url),
            )
        except Exception:
            logger.exception("发送链接风险提醒失败")
        from src.plugins.appeals import notify_superusers

        await notify_superusers(
            bot, f"⚠️ 危险链接\n群 {event.group_id}　QQ {event.user_id}\n{url}\n已在群里提醒，未自动处置，请管理员判断。")
        return
