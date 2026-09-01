"""活跃统计：/stats [yesterday] 查看白名单群消息量与发言 Top5。

- 群内：显示当前群当日（或昨日）总消息数、参与人数、Top5 发言者
- 超管私聊：显示当日所有群总览
- 数据来自 logger 插件写入的 msg_stat / msg_stat_user（只计数，不存内容）
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import BASIC
from src.store import get_store

stats_cmd = on_command("stats", rule=BASIC, priority=5, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


def _resolve_day(arg: str) -> str:
    arg = arg.strip().lower()
    if arg in ("yesterday", "昨日", "昨天"):
        return (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@stats_cmd.handle()
async def handle_stats(event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    day = _resolve_day(args.extract_plain_text())
    store = get_store()

    if isinstance(event, GroupMessageEvent):
        total, users = await store.get_group_day_stat(event.group_id, day)
        top = await store.get_top_users(event.group_id, day)
        lines = [f"群 {event.group_id} 活跃统计（{day}）", f"消息总数: {total}", f"参与人数: {users}"]
        if top:
            lines.append("发言 Top5:")
            for i, (uid, cnt) in enumerate(top, 1):
                lines.append(f"  {i}. {uid}（{cnt} 条）")
        else:
            lines.append("暂无发言记录")
        await _send(matcher, "\n".join(lines))
        return

    # 超管私聊：当日全群总览
    overview = await store.get_day_overview(day)
    if not overview:
        await _send(matcher, f"当日（{day}）暂无任何群消息记录。")
        return
    lines = [f"全群活跃总览（{day}）:"]
    for gid, cnt in overview:
        lines.append(f"  群 {gid}: {cnt} 条")
    await _send(matcher, "\n".join(lines))
