"""群投票：/投票 题目 选项1 选项2 …，大家用 /投 <序号> 投票，到时间自动公布结果。

- /投票 <题目> <选项1> <选项2> […]：发起（2–8 个选项，默认 30 分钟，每群同时只有一个进行中的投票）
- /投 <序号>：投票，投错了可以再投一次改票；一人一票
- /投票结果：查看当前票数；/结束投票：发起人或超管提前结束并公布结果
- AI 工具：create_poll / get_poll_result / end_poll（用户说"发起个投票：晚上吃啥 火锅 烧烤"时使用）
- 到时间由后台每 15 秒扫描，自动在群里公布结果
- 投票只存内存：机器人重启后进行中的投票会丢失（时长本来就短，不值得持久化）
- QQ 自带"群投票"在手机端发起；这里的优势是 AI 可直接代发，并自动公布结果
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from nonebot import get_bots, get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import superuser_ids
from src.permission import GROUP_WHITELIST

MIN_OPTIONS, MAX_OPTIONS = 2, 8
DEFAULT_MINUTES = 30
MAX_MINUTES = 24 * 60
OPTION_MAX = 30
QUESTION_MAX = 60
SCAN_SECONDS = 15

_worker_started = False


@dataclass
class Poll:
    question: str
    options: list[str]
    creator: int
    deadline: float
    votes: dict[int, int] = field(default_factory=dict)  # user_id -> 选项下标


_polls: dict[int, Poll] = {}


def create_poll(group_id: int, creator: int, question: str, options: list[str],
                minutes: int = DEFAULT_MINUTES, now: float | None = None) -> tuple[bool, str]:
    question = " ".join(question.split())[:QUESTION_MAX]
    opts = []
    for o in options:
        o = " ".join(str(o).split())[:OPTION_MAX]
        if o and o not in opts:
            opts.append(o)
    if not question:
        return False, "投票题目不能为空"
    if not (MIN_OPTIONS <= len(opts) <= MAX_OPTIONS):
        return False, f"选项需要 {MIN_OPTIONS}–{MAX_OPTIONS} 个（不能重复）"
    if group_id in _polls:
        return False, "本群已经有一个进行中的投票，等它结束或先 /结束投票"
    minutes = min(MAX_MINUTES, max(1, int(minutes)))
    now = time.time() if now is None else now
    _polls[group_id] = Poll(question, opts, creator, now + minutes * 60)
    lines = [f"📊 投票：{question}（{minutes} 分钟后截止）"]
    lines += [f"{i}. {o}" for i, o in enumerate(opts, 1)]
    lines.append("发送 /投 序号 参与，可改票")
    return True, "\n".join(lines)


def vote(group_id: int, user_id: int, index: int) -> tuple[bool, str]:
    poll = _polls.get(group_id)
    if poll is None:
        return False, "本群现在没有进行中的投票"
    if not (1 <= index <= len(poll.options)):
        return False, f"序号应在 1–{len(poll.options)} 之间"
    changed = user_id in poll.votes
    poll.votes[user_id] = index - 1
    return True, f"已{'改票为' if changed else '投给'}「{poll.options[index - 1]}」"


def result_text(poll: Poll, final: bool = False) -> str:
    counts = [0] * len(poll.options)
    for i in poll.votes.values():
        counts[i] += 1
    total = len(poll.votes)
    lines = [f"📊 {'投票结果' if final else '当前票数'}：{poll.question}（共 {total} 人参与）"]
    top = max(counts) if counts else 0
    for i, (o, c) in enumerate(zip(poll.options, counts), 1):
        pct = f"{c * 100 // total}%" if total else "0%"
        crown = " 👑" if final and total and c == top else ""
        lines.append(f"{i}. {o}：{c} 票（{pct}）{crown}")
    return "\n".join(lines)


def get_result(group_id: int) -> str:
    poll = _polls.get(group_id)
    return result_text(poll) if poll else "本群现在没有进行中的投票"


def end_poll(group_id: int, user_id: int, force_any: bool = False) -> tuple[bool, str]:
    poll = _polls.get(group_id)
    if poll is None:
        return False, "本群现在没有进行中的投票"
    if not force_any and user_id != poll.creator and user_id not in superuser_ids():
        return False, "只有发起人或超管能提前结束投票"
    _polls.pop(group_id, None)
    return True, result_text(poll, final=True)


# ---------------------------------------------------------------- 指令

poll_cmd = on_command("投票", aliases={"poll"}, rule=GROUP_WHITELIST, priority=5, block=True)
vote_cmd = on_command("投", aliases={"vote"}, rule=GROUP_WHITELIST, priority=5, block=True)
result_cmd = on_command("投票结果", rule=GROUP_WHITELIST, priority=4, block=True)
end_cmd = on_command("结束投票", rule=GROUP_WHITELIST, priority=4, block=True)


@poll_cmd.handle()
async def handle_poll(matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().replace("｜", " ").replace("|", " ").split()
    if len(parts) < 1 + MIN_OPTIONS:
        await matcher.finish("用法：/投票 <题目> <选项1> <选项2> […]\n例：/投票 晚上吃啥 火锅 烧烤 麻辣烫")
    _, msg = create_poll(event.group_id, event.user_id, parts[0], parts[1:])
    await matcher.send(msg)


@vote_cmd.handle()
async def handle_vote(matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip()
    if not arg.isdigit():
        await matcher.finish("用法：/投 <序号>")
    _, msg = vote(event.group_id, event.user_id, int(arg))
    await matcher.send(msg)


@result_cmd.handle()
async def handle_result(matcher: Matcher, event: GroupMessageEvent) -> None:
    await matcher.send(get_result(event.group_id))


@end_cmd.handle()
async def handle_end(matcher: Matcher, event: GroupMessageEvent) -> None:
    _, msg = end_poll(event.group_id, event.user_id)
    await matcher.send(msg)


# ---------------------------------------------------------------- 到期公布

async def announce_expired(bot: Bot, now: float | None = None) -> int:
    now = time.time() if now is None else now
    done = [g for g, p in _polls.items() if p.deadline <= now]
    for gid in done:
        poll = _polls.pop(gid)
        try:
            await bot.call_api("send_group_msg", group_id=gid, message="⏰ 投票时间到！\n" + result_text(poll, final=True))
        except Exception:
            logger.exception(f"公布投票结果失败：group={gid}")
    return len(done)


async def _worker() -> None:
    while True:
        await asyncio.sleep(SCAN_SECONDS)
        try:
            for bot in list(get_bots().values()):
                if isinstance(bot, Bot):
                    await announce_expired(bot)
                    break
        except Exception:
            logger.exception("投票到期扫描异常")


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())
