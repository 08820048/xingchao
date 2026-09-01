"""群聊 @机器人 响应：白名单群成员 @星潮（或昵称呼喊）时给出回应。

- OneBot V11 适配器会把指向自身的 at 段从消息中剥离并置 event.to_me=True，
  因此本插件以 to_me 判定，而不是扫描消息段。
- 成员 @ 机器人且不是指令时：
  - 纯 @（无文字）→ 简短回应
  - 内容含「帮助 / help」→ 回复帮助菜单（通常已被 basic 的别名抢先，此处兜底）
  - 其他内容 → 简短回应 + 提示 /help
- 同群冷却（默认与关键词回复一致，取 XINGCHAO_REPLY_COOLDOWN），避免刷屏
"""

from __future__ import annotations

import time

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher

from src.config import get_config
from src.permission import GROUP_WHITELIST

mention_matcher = on_message(rule=GROUP_WHITELIST, priority=10, block=False)

_last_hit: dict[int, float] = {}

GREETING = "在的～发送 /help 查看我能做什么。"


def _is_command(text: str) -> bool:
    start = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in start)


async def _send(matcher: Matcher, message: MessageSegment | str) -> None:
    try:
        await matcher.send(message)
    except MatcherException:
        raise
    except Exception:
        logger.exception("@响应发送失败")


@mention_matcher.handle()
async def handle_mention(event: GroupMessageEvent, matcher: Matcher) -> None:
    if not event.to_me:
        return

    text = event.message.extract_plain_text().strip()
    if _is_command(text):
        return  # 指令交给指令 matcher

    # 内容含「帮助 / help」优先回帮助菜单（无论 AI 是否开启）
    lowered = text.lower()
    if text and ("帮助" in text or "help" in lowered):
        from src.plugins import basic as basic_plugin

        await _send(matcher, basic_plugin.HELP_TEXT)
        return

    # AI 已启用时由 ai 插件接管对话（priority 11）
    from src.plugins import ai as ai_plugin

    if await ai_plugin.is_ai_enabled():
        return

    # 冷却：同群默认 8 秒内不重复回应
    now = time.monotonic()
    last = _last_hit.get(event.group_id)
    if last is not None and now - last < get_config().xingchao_reply_cooldown:
        logger.debug(f"@响应在群 {event.group_id} 冷却期内，跳过")
        return
    _last_hit[event.group_id] = now

    await _send(matcher, MessageSegment.at(event.user_id) + " " + GREETING)
