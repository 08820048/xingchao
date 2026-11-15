"""群知识库：把「这个群反复被问的事」沉淀成可命中的条目。

为什么要有：群里总有几个问题隔三差五被问一遍（怎么申请内测、群规在哪、报名表在哪），
答案每次都要有人重打一遍。本模块提供：

1. 条目管理（超管）：`/知识 添加 问题 | 答案 [| 关键词]`、`/知识 列表`、`/知识 删除 <编号>`
2. 自动命中（白名单群，`/模块 知识 开|关`）：群里有人问出来的话命中条目时，直接给出答案。
   默认只在「看起来是在提问」时回答（问号、怎么/如何/在哪 之类），或者强烈命中关键词，
   避免正常聊天被硬塞答案；同群有冷却，不刷屏
3. 自动发现：把最近被问到、但知识库里没有的问题聚成候选项，超管在面板一键采纳，
   `/知识 建议` 也能看。这是让知识库「自己长起来」的关键，否则没人愿意手动录入

命中判定复用 src/textindex.py 的分词（与群聊回忆、博客知识库同一套口径），
按「问题 + 关键词」的词项覆盖率打分，命中即计数（hits 用于排序和运营判断）。

已知取舍：n-gram 打分在临界区间分不开「内测怎么搞」（应命中）和「今天会员日吗」
（对「怎么加入会员群」其实不该命中）——两者都只共享一个双字词，得分相同。
因此默认阈值取 1.8（宁可命中），并用三重手段兜底：
  1. kb_require_question 默认开：只回答「像提问」的消息，陈述句不抢答；
  2. 回答里带上命中的问题（【问题】+ 答案），错了一看就知道，不是无源之水；
  3. 面板可调 kb_min_score、可见每条 hits，坏条目能定位到具体一条并删改。

隐私：只存你写进去的问答，不存聊天原文；自动发现只统计问题文本本身。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from nonebot import on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.cmdutil import norm_action
from src.permission import GROUP_WHITELIST, SUPERUSER, merged_whitelist
from src.store import get_store
from src.textindex import term_weight, terms as extract_terms

DEFAULTS: dict[str, Any] = {
    "kb_enabled": True,
    "kb_auto_answer": True,      # 群里命中时自动回答
    "kb_cooldown": 300,          # 同群回答冷却（秒）
    "kb_min_score": 1.8,         # 命中阈值
    "kb_require_question": True, # 只在像提问时主动回答
    "kb_suggest_min": 2,         # 同一个问题被问几次才作为候选
}

QUESTION_HINTS = re.compile(
    r"(\?|？|怎么|如何|怎样|在哪|哪里|多少|能不能|可以吗|有没有|求|谁有|请问|问一下|"
    r"想问|求助|咋办|咋整|咋用|是什么|啥是|在哪下|哪下载)"
)
MAX_QUESTION = 100
MAX_ANSWER = 200
SUGGEST_LOOKBACK_HOURS = 72
_last_answer: dict[int, float] = {}


def _reset_state() -> None:
    """测试用：清空回答冷却与候选缓存。"""
    _last_answer.clear()


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw == "true"
    if isinstance(default, int):
        try:
            return max(0, int(raw))
        except ValueError:
            return default
    return raw


async def is_enabled() -> bool:
    return bool(await _kv("kb_enabled"))


def looks_like_question(text: str) -> bool:
    return bool(QUESTION_HINTS.search(text or ""))


# 人工录入的关键词是更强的信号：命中它的分值是命中问题原文的 1.5 倍
KEYWORD_BOOST = 1.5


def _entry_terms(entry: dict, *, with_keywords: bool = True) -> set[str]:
    text = entry["question"]
    if with_keywords and entry.get("keywords"):
        text += " " + entry["keywords"]
    return extract_terms(text)


def score_entry(entry: dict, query_terms: set[str]) -> float:
    """问题/关键词与提问的词项重合度（0 表示不命中）。

    - 命中问题原文：按词项权重求和
    - 命中人工关键词：额外 ×KEYWORD_BOOST（管理员显式写的同义说法，比原文更可信）
    - 覆盖率用「条目词被覆盖的比例」，问题短、关键词少的条目更容易命中
    """
    if not query_terms:
        return 0.0
    question_terms = extract_terms(entry["question"])
    keyword_terms = extract_terms(entry.get("keywords") or "")
    entry_terms = question_terms | keyword_terms
    if not entry_terms:
        return 0.0
    hit = query_terms & entry_terms
    if not hit:
        return 0.0
    weight = 0.0
    for term in hit:
        w = term_weight(term)
        if term in keyword_terms and term not in question_terms:
            w *= KEYWORD_BOOST
        weight += w
    # 覆盖率分母封顶 4：问句往往只含一两个核心词（「内测怎么搞」只命中「内测」），
    # 分母按条目总词数算会把这个唯一的核心词惩罚得过狠，导致该命中却命中不上
    coverage = min(len(hit) / min(len(entry_terms), 4), 1.0)
    return weight * (0.6 + 0.4 * coverage)


def rank_entries(entries: list[dict], query: str) -> list[tuple[float, dict]]:
    query_terms = extract_terms(query)
    if not query_terms:
        return []
    scored = [(score_entry(e, query_terms), e) for e in entries]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], -pair[1].get("hits", 0)))
    return scored


async def match(group_id: int, text: str, *, min_score: float | None = None) -> dict | None:
    """找出最匹配的一条知识（给指令、AI 工具、自动命中共用）。"""
    entries = await get_store().list_kb_entries(group_id)
    if not entries:
        return None
    ranked = rank_entries(entries, text)
    if not ranked:
        return None
    threshold = float(await _kv("kb_min_score")) if min_score is None else min_score
    score, entry = ranked[0]
    return entry if score >= threshold else None


async def answer_for(group_id: int, text: str) -> dict | None:
    """命中就返回条目（并计数），否则 None。

    返回条目而不是纯文本，是为了回答时把「命中的是哪个问题」一并显示出来——
    n-gram 匹配在临界区间不可避免会有偏差，让群友一眼能看出对错了，方便管理员修条目。
    """
    if not await is_enabled():
        return None
    if not bool(await _kv("kb_auto_answer")):
        return None
    if bool(await _kv("kb_require_question")) and not looks_like_question(text):
        return None
    entry = await match(group_id, text)
    if entry is None:
        return None
    try:
        await get_store().bump_kb_hits(entry["id"])
    except Exception:
        logger.debug("知识命中计数失败", exc_info=True)
    return entry


# ---------------------------------------------------------------- 自动发现候选项

_QUESTION_TAIL = re.compile(r"[?？\s]+$")
# 提问时的客套/呼唤前缀，聚类前去掉，否则「请问报名表在哪」和「报名表在哪」会被算成两个问题
_POLITE_PREFIX = re.compile(
    r"^(?:请问|想问一下|想问下|想问|问一下|问下|求问|求助|问个事|"
    r"有没有人知道|有人知道|有人知道吗|大佬们|大佬|兄弟们|各位|打扰一下|"
    r"麻烦问一下|麻烦问下|想问一问)+"
)


def normalize_question(text: str) -> str:
    """把一句提问归一成可聚类的主键：去客套前缀、标点空白、转小写、截断。"""
    text = _QUESTION_TAIL.sub("", (text or "").strip())
    text = re.sub(r"[\s，,。.！!？?~]+", "", text)
    text = _POLITE_PREFIX.sub("", text)
    return text[:MAX_QUESTION]


async def suggest(group_id: int, *, min_count: int | None = None) -> list[dict]:
    """从群聊日志里找出「反复被问、但知识库没有」的问题候选。

    只统计像提问的短消息，按归一化文本聚类，出现次数达到阈值才返回；
    已经在知识库里能命中的问题不重复推荐。
    """
    from src.plugins import recall

    if min_count is None:
        min_count = int(await _kv("kb_suggest_min"))
    try:
        await recall.refresh([group_id])
    except Exception:
        logger.debug("刷新群聊索引失败（知识候选）", exc_info=True)

    bucket = recall._docs.get(group_id) or []
    since = datetime.now().astimezone() - timedelta(hours=SUGGEST_LOOKBACK_HOURS)
    counts: dict[str, dict] = {}
    for doc in bucket:
        if doc.ts < since:
            continue
        text = doc.text.strip()
        if not (4 <= len(text) <= MAX_QUESTION) or not looks_like_question(text):
            continue
        key = normalize_question(text)
        if len(key) < 4:
            continue
        item = counts.setdefault(key, {"question": text[:MAX_QUESTION], "count": 0})
        item["count"] += 1

    existing = await get_store().list_kb_entries(group_id)
    out: list[dict] = []
    for item in counts.values():
        if item["count"] < min_count:
            continue
        ranked = rank_entries(existing, item["question"]) if existing else []
        if ranked and ranked[0][0] >= float(await _kv("kb_min_score")):
            continue  # 已经能命中了，不用再提
        out.append(item)
    out.sort(key=lambda i: -i["count"])
    return out[:20]


# ---------------------------------------------------------------- 群内自动回答

kb_matcher = on_message(rule=GROUP_WHITELIST, priority=6, block=False)


async def _notify_answer(matcher: Matcher, event: GroupMessageEvent, entry: dict) -> None:
    try:
        await matcher.send(
            MessageSegment.at(event.user_id)
            + f"\n【{entry['question']}】\n{entry['answer']}"
        )
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送知识库回答失败")


@kb_matcher.handle()
async def handle_kb(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id):
        return
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return
    now = time.time()
    cooldown = int(await _kv("kb_cooldown"))
    if cooldown and now - _last_answer.get(event.group_id, 0.0) < cooldown:
        return  # 同群冷却，避免连着几条都抢答
    entry = await answer_for(event.group_id, text)
    if entry is None:
        return
    _last_answer[event.group_id] = now
    await _notify_answer(matcher, event, entry)


# ---------------------------------------------------------------- 指令

kb_cmd = on_command("知识", aliases={"kb"}, rule=SUPERUSER, priority=1, block=True)


def _parse_add(raw: str) -> tuple[str, str, str] | None:
    """`问题 | 答案 | 关键词`（关键词可省略）。"""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    keywords = parts[2] if len(parts) > 2 else ""
    return parts[0][:200], parts[1][:2000], keywords[:300]


@kb_cmd.handle()
async def handle_kb_cmd(bot: Bot, event: MessageEvent, matcher: Matcher,
                        args: Message = CommandArg()) -> None:
    raw = norm_action(args.extract_plain_text())
    action, _, rest = raw.partition(" ")
    action, rest = action.strip(), rest.strip()
    store = get_store()
    group_id = getattr(event, "group_id", None)

    if action in ("列表", "list", ""):
        entries = await store.list_kb_entries(group_id)
        if not entries:
            await matcher.finish(
                "这个群的知识库还是空的。\n"
                "用法：/知识 添加 问题 | 答案 [| 关键词]\n"
                "例：/知识 添加 怎么申请内测 | 私聊管理员报 QQ 和用途 | 内测 申请"
            )
        lines = [f"本群知识库（{len(entries)} 条）："]
        for e in entries[:30]:
            lines.append(f"#{e['id']} {e['question']}（命中 {e['hits']} 次）")
        if len(entries) > 30:
            lines.append(f"……还有 {len(entries) - 30} 条，去面板「知识库」页看全部")
        await matcher.finish("\n".join(lines))

    if action in ("添加", "add"):
        parsed = _parse_add(rest)
        if parsed is None:
            await matcher.finish(
                "用法：/知识 添加 问题 | 答案 [| 关键词]\n"
                "例：/知识 添加 群规在哪 | 群公告第一条 | 群规 规则"
            )
        question, answer, keywords = parsed
        if not group_id:
            await matcher.finish("请在群里添加（知识按群隔离）。")
        entry_id = await store.add_kb_entry(
            group_id, question, answer, keywords,
            source="manual", created_by=int(event.user_id),
        )
        await matcher.finish(f"已添加知识 #{entry_id}：{question}\n有人问到就会自动回答。")

    if action in ("删除", "del"):
        if not rest.isdigit():
            await matcher.finish("用法：/知识 删除 <编号>（编号看 /知识 列表）")
        entry = await store.get_kb_entry(int(rest))
        if entry is None or (group_id and entry["group_id"] != group_id):
            await matcher.finish("没有这个编号的知识。")
        await store.delete_kb_entry(int(rest))
        await matcher.finish(f"已删除知识 #{rest}：{entry['question']}")

    if action in ("改", "编辑", "set"):
        num, _, tail = rest.partition(" ")
        if not num.isdigit():
            await matcher.finish("用法：/知识 改 <编号> <新的答案>")
        entry = await store.get_kb_entry(int(num))
        if entry is None or (group_id and entry["group_id"] != group_id):
            await matcher.finish("没有这个编号的知识。")
        if not tail.strip():
            await matcher.finish("请给出新的答案内容。")
        await store.update_kb_entry(int(num), answer=tail.strip()[:2000])
        await matcher.finish(f"已更新知识 #{num} 的答案。")

    if action in ("测试", "test"):
        if not rest:
            await matcher.finish("用法：/知识 测试 <一句话>，看看会不会命中")
        entry = await match(group_id, rest) if group_id else None
        if entry is None:
            await matcher.finish(f"「{rest}」没有命中任何知识。")
        await matcher.finish(f"命中 #{entry['id']}：{entry['question']}\n答案：{entry['answer']}")

    if action in ("建议", "suggest", "候选"):
        suggestions = await suggest(group_id) if group_id else []
        if not suggestions:
            await matcher.finish("最近没有反复被问、又不在知识库里的问题。")
        lines = ["最近被反复问、知识库还没收录的问题："]
        lines += [f"{i}. {s['question']}（{s['count']} 次）" for i, s in enumerate(suggestions[:10], 1)]
        lines.append("采纳：/知识 添加 问题 | 答案")
        await matcher.finish("\n".join(lines))

    if action in ("开", "on", "关", "off"):
        enable = action in ("开", "on")
        await store.set_kv("kb_enabled", "true" if enable else "false")
        await matcher.finish(f"群知识库已{'开启' if enable else '关闭'}。")

    await matcher.finish(
        "群知识库用法：\n"
        "/知识 添加 问题 | 答案 [| 关键词]\n"
        "/知识 列表　/知识 删除 <编号>　/知识 改 <编号> <答案>\n"
        "/知识 测试 <一句话>　/知识 建议（最近反复被问的问题）\n"
        "/知识 开|关"
    )


async def panel_payload(group_id: int | None = None) -> dict[str, Any]:
    """面板「知识库」页数据。"""
    store = get_store()
    entries = await store.list_kb_entries(group_id)
    return {
        "entries": [{**e, "group_id": e["group_id"]} for e in entries],
        "groups": sorted(merged_whitelist()),
        "enabled": await is_enabled(),
        "auto_answer": bool(await _kv("kb_auto_answer")),
        "require_question": bool(await _kv("kb_require_question")),
        "cooldown": int(await _kv("kb_cooldown")),
        "min_score": float(await _kv("kb_min_score")),
        "suggest_min": int(await _kv("kb_suggest_min")),
    }

