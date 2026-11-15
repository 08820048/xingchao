"""群聊回忆：把「群里以前说过什么」变成可检索的记忆。

为什么要有：群里的会话是按天滚动的，说过就沉底了——「上周谁发过那个 Rust 教程」
「团购的表格在哪」「群规说过不能干啥」这类问题，只能靠人肉翻聊天记录。本模块把
data/logs/group-*.jsonl 增量读进**内存**并支持按关键词检索，让机器人和群友都能
一句话把旧消息捞回来。

隐私约定（硬约束）：聊天原文**不进数据库**。索引全部在内存里（与 awareness 的
群聊缓冲同一原则），进程重启后从 jsonl 重新构建，不产生任何持久化副本；本模块
只读 logger.py 已经写下的日志，不额外落盘。

对外：
- /回忆 <关键词>      在白名单群检索最近 recall_days 天的聊天（默认 90 天）
- /回忆 状态          查看已索引多少条、覆盖到哪天
- AI 工具 recall_chat 让 AI 自己检索（可带 days，模型能把「上周」翻成天数）

实现要点：
- 增量：按文件记录已读到的**字节偏移**，只读新追加的部分；末尾不完整的行不推进偏移，
  避免把半行 JSON 读坏
- 容量：每群保留最近 recall_max_docs 条（默认 2 万）；超出丢弃更早的，避免内存膨胀
- 检索：中文 2-4 字 n-gram + 英文单词（src/textindex.py 与博客知识库共用），
  暴力打分够快（单群 2 万条量级），不维护倒排索引也就没有索引腐烂问题
- 只在白名单群生效；空文本（纯图/纯表情）不入索引
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.background import periodic
from src.config import get_config
from src.permission import BASIC, merged_whitelist
from src.store import get_store
from src.textindex import term_weight, terms as extract_terms

DEFAULTS: dict[str, Any] = {
    "recall_enabled": True,
    "recall_days": 90,          # 默认检索窗口（天）
    "recall_max_docs": 20000,   # 每群在内存里最多保留多少条
    "recall_results": 5,        # 每次最多返回几条
    "recall_min_score": 1.0,    # 低于这个分数不返回
}

MAX_QUERY_CHARS = 60
SNIPPET_CHARS = 160
NAME_CACHE_TTL = 3600


@dataclass(slots=True)
class Doc:
    group_id: int
    message_id: int
    ts: datetime
    user_id: int
    text: str


_docs: dict[int, deque[Doc]] = {}
_loaded: dict[Path, int] = {}      # 文件 → 已读字节偏移
_files_seen: set[Path] = set()
_name_cache: dict[tuple[int, int], tuple[float, str]] = {}


def _reset_state() -> None:
    """测试用：清空内存索引与缓存。"""
    _docs.clear()
    _loaded.clear()
    _files_seen.clear()
    _name_cache.clear()


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
    return bool(await _kv("recall_enabled"))


# ---------------------------------------------------------------- 增量读取

def _log_paths(log_dir: Path, group_ids: Iterable[int] | None = None) -> list[Path]:
    if not log_dir.exists():
        return []
    allowed = set(group_ids) if group_ids is not None else None
    out: list[Path] = []
    for path in log_dir.glob("group-*.jsonl"):
        m = re.match(r"group-(-?\d+)-", path.name)
        if not m:
            continue
        if allowed is not None and int(m.group(1)) not in allowed:
            continue
        out.append(path)
    return sorted(out)


def _parse_record(raw: bytes, group_id: int) -> Doc | None:
    try:
        rec = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    text = str(rec.get("raw_plain") or "").strip()
    if not text:
        return None  # 纯图片 / 纯表情不占额度
    try:
        ts = datetime.fromisoformat(str(rec.get("time") or ""))
    except ValueError:
        return None
    try:
        return Doc(
            group_id=int(rec.get("group_id") or group_id),
            message_id=int(rec.get("message_id") or 0),
            ts=ts,
            user_id=int(rec.get("user_id") or 0),
            text=text[:500],
        )
    except (TypeError, ValueError):
        return None


def ingest_file(path: Path, max_docs: int, group_id: int | None = None) -> int:
    """把文件里**新增**的完整行读进内存，返回新增条数。

    只在遇到完整换行时推进偏移，避免读到写了一半的行。
    """
    m = re.match(r"group-(-?\d+)-", path.name)
    if group_id is None:
        if not m:
            return 0
        group_id = int(m.group(1))

    offset = _loaded.get(path, 0)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size < offset:      # 文件被截断/重建：从头读
        offset = 0
    if size == offset:
        return 0

    try:
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        logger.debug(f"读取群聊日志失败：{path}", exc_info=True)
        return 0

    last_nl = chunk.rfind(b"\n")
    if last_nl < 0:
        return 0  # 还没有完整的一行
    body, consumed = chunk[: last_nl + 1], last_nl + 1

    bucket = _docs.setdefault(group_id, deque(maxlen=max_docs))
    if bucket.maxlen != max_docs:
        # 面板调小了保留条数：立刻按新上限收敛，不用等重启
        _docs[group_id] = bucket = deque(bucket, maxlen=max_docs)
    added = 0
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        doc = _parse_record(line, group_id)
        if doc is not None:
            bucket.append(doc)
            added += 1
    _loaded[path] = offset + consumed
    _files_seen.add(path)
    return added


async def refresh(group_ids: Iterable[int] | None = None, *, force: bool = False) -> int:
    """扫描日志目录，把新追加的消息读进内存；返回新增条数。"""
    if not await is_enabled():
        return 0
    max_docs = int(await _kv("recall_max_docs"))
    log_dir = get_config().xingchao_log_dir
    allowed = set(group_ids) if group_ids else merged_whitelist()
    total = 0
    for path in _log_paths(log_dir, allowed):
        total += ingest_file(path, max_docs)
    return total


@periodic("群聊检索索引", 300, wait_bot=False, run_first=True)
async def _periodic_refresh(_bot) -> None:
    try:
        await refresh()
    except Exception:
        logger.exception("群聊检索索引刷新失败")


# ---------------------------------------------------------------- 检索

def _score(doc: Doc, query_terms: set[str], now: datetime) -> float:
    doc_terms = extract_terms(doc.text)
    if not doc_terms:
        return 0.0
    hit = query_terms & doc_terms
    if not hit:
        return 0.0
    # 命中的词越长越具体，权重越高；同时要求覆盖查询词的比例，避免只蹭到一个常用词
    base = sum(term_weight(t) for t in hit)
    coverage = len(hit) / len(query_terms)
    score = base * (0.4 + 0.6 * coverage)
    # 近期消息略加分（最多 +30%），让「上周说的」优先于「半年前说的」
    age_days = max(0.0, (now - doc.ts).total_seconds() / 86400)
    score *= 1.0 + 0.3 * max(0.0, 1 - age_days / 90)
    return score


def search_sync(
    group_id: int, query: str, *, days: int | None = None, limit: int = 5, min_score: float = 1.0,
) -> list[tuple[float, Doc]]:
    """在内存索引里检索（同步，供测试与已刷新过的场景调用）。"""
    query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return []
    query_terms = extract_terms(query)
    if not query_terms:
        return []
    bucket = _docs.get(group_id)
    if not bucket:
        return []

    now = datetime.now().astimezone()
    window = days if days is not None else 90
    since = now - timedelta(days=window) if window > 0 else None

    scored: list[tuple[float, Doc]] = []
    for doc in reversed(bucket):        # 新的先看，命中够了可以少扫
        if since is not None and doc.ts < since:
            continue
        s = _score(doc, query_terms, now)
        if s >= min_score:
            scored.append((s, doc))
            if len(scored) >= limit * 8:  # 先收一批再排序，避免全量打分
                break
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


async def search(
    group_id: int, query: str, *, days: int | None = None, limit: int | None = None,
) -> list[tuple[float, Doc]]:
    """检索前先增量刷新，保证刚说过的话也能搜到。"""
    if not await is_enabled():
        return []
    await refresh([group_id])
    if days is None:
        days = int(await _kv("recall_days"))
    if limit is None:
        limit = int(await _kv("recall_results"))
    hits = search_sync(
        group_id, query, days=days, limit=limit, min_score=float(await _kv("recall_min_score")),
    )
    if hits:  # 只统计真正翻到东西的次数，避免把"随手一试"也算成价值
        try:
            await get_store().incr_action(group_id, "recall_query")
        except Exception:
            logger.debug("回忆查询计数失败", exc_info=True)
    return hits


async def _display_name(bot: Bot | None, group_id: int, user_id: int) -> str:
    key = (group_id, user_id)
    now_ts = datetime.now().timestamp()
    cached = _name_cache.get(key)
    if cached and now_ts - cached[0] < NAME_CACHE_TTL:
        return cached[1]
    name = str(user_id)
    if bot is not None:
        try:
            info = await bot.call_api(
                "get_group_member_info", group_id=group_id, user_id=user_id, no_cache=False
            )
            name = str(info.get("card") or info.get("nickname") or user_id)
        except Exception:
            logger.debug(f"获取群成员昵称失败：{group_id}/{user_id}", exc_info=True)
    _name_cache[key] = (now_ts, name)
    return name


def format_hits(hits: list[tuple[float, Doc]], names: dict[int, str] | None = None) -> str:
    """把检索结果渲染成 QQ 文本（时间 + 发言人 + 原文）。"""
    names = names or {}
    lines = []
    for _score_value, doc in hits:
        when = doc.ts.strftime("%m-%d %H:%M")
        who = names.get(doc.user_id) or str(doc.user_id)
        text = " ".join(doc.text.split())
        if len(text) > SNIPPET_CHARS:
            text = text[:SNIPPET_CHARS] + "…"
        lines.append(f"[{when}] {who}：{text}")
    return "\n".join(lines)


async def recall_text(bot: Bot | None, group_id: int, query: str, days: int | None = None) -> str:
    """给指令与 AI 工具共用的入口：检索 + 取昵称 + 渲染。"""
    hits = await search(group_id, query, days=days)
    if not hits:
        window = days if days is not None else int(await _kv("recall_days"))
        return f"最近 {window} 天里没找到和「{query}」相关的聊天。"
    names = {doc.user_id: await _display_name(bot, group_id, doc.user_id) for _s, doc in hits}
    header = f"关于「{query}」找到 {len(hits)} 条："
    return header + "\n" + format_hits(hits, names)


def stats_sync() -> dict[str, Any]:
    """索引概况（面板 / /回忆 状态 用）。"""
    per_group = {gid: len(bucket) for gid, bucket in _docs.items() if bucket}
    newest = max((d.ts for b in _docs.values() for d in b), default=None)
    oldest = min((d.ts for b in _docs.values() for d in b), default=None)
    return {
        "groups": per_group,
        "total": sum(per_group.values()),
        "files": len(_files_seen),
        "oldest": oldest.isoformat(timespec="seconds") if oldest else "",
        "newest": newest.isoformat(timespec="seconds") if newest else "",
    }


# ---------------------------------------------------------------- 指令

recall_cmd = on_command("回忆", aliases={"recall"}, rule=BASIC, priority=5, block=True)


@recall_cmd.handle()
async def handle_recall(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()) -> None:
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("请在群里用 /回忆 <关键词>。")
    if not await is_enabled():
        await matcher.finish("群聊回忆功能当前已关闭。")
    query = args.extract_plain_text().strip()
    if not query:
        await matcher.finish("用法：/回忆 <关键词>，例如 /回忆 那个表格\n想看索引状态：/回忆 状态")
    if query in ("状态", "status"):
        await refresh()
        st = stats_sync()
        if not st["total"]:
            await matcher.finish("还没有索引到消息（日志目录为空，或功能刚开启）。")
        await matcher.finish(
            f"已索引 {st['total']} 条消息，覆盖 {len(st['groups'])} 个群、{st['files']} 个日志文件\n"
            f"时间范围：{(st['oldest'] or '')[:16]} ~ {(st['newest'] or '')[:16]}"
        )
    await matcher.send(await recall_text(bot, event.group_id, query))
