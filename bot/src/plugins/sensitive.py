"""敏感词监控：命中候选经规则 + AI 复核后撤回，可选禁言与通知超管（防广告/敏感信息）。

- 监听白名单群消息（priority=3，命中后 stop_propagation，不再触发 AI/关键词回复）
- 配置分全局默认 + 每群覆盖（面板「敏感词」页可改，持久化 kv，即时生效）
- 词条以 ~ 开头表示豁免短语（匹配前从消息中剔除，如 ~机场大巴 避免「机场」误伤）
- 纯英文/数字词条按词边界匹配（PIA 不会命中 Olympiad）
- 变体匹配（sensitive_variant_level）：basic=原样；normal=归一化（全角/空格标点/繁简）；
  pinyin=归一化+拼音（拦「加 微 信」「ＶＰＮ」「廣告」「weixin/威信」等绕过写法）
- 命中候选默认交 AI 结合语境复核：只有确认违规才撤回/禁言，正常讨论直接放行；
  AI 不可用时按 fallback 处理（默认 recall：直接按程序规则撤回，不等人工）
- 动作：撤回原消息；mute_minutes > 0 时追加禁言；notify 开启时通知超管
- 撤回失败（机器人非群管理员等）会如实转述给超管
- 升级处罚（sensitive_escalation）：同一人在同一群窗口期（默认 30 天）内按已确认违规次数递进——
  第 1 次=当前规则；第 2 次=禁言翻倍；第 3 次=最长禁言（30 天）；第 4 次起=踢出群聊（无需确认）。
  AI 复核不可用时按兜底撤回的记录不计入次数；申诉通过的记录不再计数
- 踢人防线：群主/管理员/超管/豁免名单永不自动踢；每群每日踢出上限（默认 3）；
  升级处罚首次启用后有试运行期（默认 7 天）只禁言不踢，期间只通知超管「本应踢出」
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from typing import Any, NamedTuple

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import Rule
from opencc import OpenCC
from pypinyin import lazy_pinyin

from src.permission import GROUP_WHITELIST, SUPERUSER
from src.store import get_store

try:
    _t2s = OpenCC("t2s").convert
except Exception:  # 词典缺失等异常时退化为不做繁简转换

    def _t2s(text: str) -> str:
        return text

DEFAULTS: dict[str, Any] = {
    "sensitive_enabled": False,  # 默认关闭，需在面板开启
    "sensitive_words": "",  # 逗号分隔；~前缀为豁免短语
    "sensitive_mute_minutes": 0,  # 命中后禁言分钟数，0 = 不禁言
    "sensitive_notify": True,  # 命中后通知超管
    "sensitive_review": True,  # 命中候选后由 AI 结合语境复核
    "sensitive_review_fallback": "recall",  # AI 不可用：recall=按程序规则照常撤回 / notify=仅通知不撤回
    "sensitive_escalation": True,  # 重复违规逐级加重：禁言翻倍 → 最长禁言 → 踢出
    "sensitive_strike_days": 30,  # 违规次数统计窗口（天），窗口外的记录不再累计
    "sensitive_kick_daily_cap": 3,  # 每群每日自动踢出人数上限，0 = 不限
    "sensitive_trial_days": 7,  # 升级处罚试运行天数：期间「第 4 次」只最长禁言不踢，0 = 无试运行
    "sensitive_exempt_users": "",  # 踢人豁免名单（QQ，逗号分隔），仍会撤回/禁言
    "sensitive_variant_level": "normal",  # 变体匹配：basic=原样 / normal=归一化 / pinyin=归一化+拼音
}
_VARIANT_LEVELS = ("basic", "normal", "pinyin")


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


@get_driver().on_startup
async def _migrate_fallback_recall() -> None:
    """一次性迁移：AI 复核不可用的兜底由「仅通知」改为「直接撤回」，覆盖旧的已保存值。"""
    store = get_store()
    if await store.get_kv("sensitive_fallback_recall_migrated"):
        return
    await store.set_kv("sensitive_review_fallback", "recall")
    overrides = await _group_overrides()
    changed = False
    for fields in overrides.values():
        if fields.get("sensitive_review_fallback") == "notify":
            fields["sensitive_review_fallback"] = "recall"
            changed = True
    if changed:
        await store.set_kv("sensitive_group_config", json.dumps(overrides, ensure_ascii=False))
    await store.set_kv("sensitive_fallback_recall_migrated", "1")


async def _group_overrides() -> dict[int, dict[str, Any]]:
    raw = await get_store().get_kv("sensitive_group_config")
    if not raw:
        return {}
    try:
        return {int(g): v for g, v in json.loads(raw).items()}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


async def save_group_override(group_id: int, fields: dict[str, Any]) -> None:
    overrides = await _group_overrides()
    overrides[group_id] = fields
    await get_store().set_kv("sensitive_group_config", json.dumps(overrides, ensure_ascii=False))


async def clear_group_override(group_id: int) -> bool:
    overrides = await _group_overrides()
    if group_id not in overrides:
        return False
    overrides.pop(group_id)
    await get_store().set_kv("sensitive_group_config", json.dumps(overrides, ensure_ascii=False))
    return True


async def sensitive_config(group_id: int | None = None) -> dict[str, Any]:
    """生效配置 = 全局默认 + 该群覆盖。"""
    global_cfg = {k: await _kv(k) for k in DEFAULTS}
    if group_id is None:
        return global_cfg
    override = (await _group_overrides()).get(group_id, {})
    merged = {**global_cfg, **{k: v for k, v in override.items() if k in DEFAULTS}}
    lr = merged["sensitive_enabled"]
    merged["sensitive_enabled"] = lr is True or (isinstance(lr, str) and lr != "false")
    merged["sensitive_notify"] = (
        merged["sensitive_notify"] is True
        or (isinstance(merged["sensitive_notify"], str) and merged["sensitive_notify"] != "false")
    )
    merged["sensitive_mute_minutes"] = int(merged["sensitive_mute_minutes"] or 0)
    review = merged["sensitive_review"]
    merged["sensitive_review"] = review is True or (
        isinstance(review, str) and review != "false"
    )
    esc = merged["sensitive_escalation"]
    merged["sensitive_escalation"] = esc is True or (isinstance(esc, str) and esc != "false")
    merged["sensitive_strike_days"] = max(1, int(merged["sensitive_strike_days"] or 30))
    merged["sensitive_kick_daily_cap"] = max(0, int(merged["sensitive_kick_daily_cap"] or 0))
    merged["sensitive_trial_days"] = max(0, int(merged["sensitive_trial_days"] or 0))
    if merged["sensitive_review_fallback"] not in ("notify", "recall"):
        merged["sensitive_review_fallback"] = "recall"
    if merged["sensitive_variant_level"] not in _VARIANT_LEVELS:
        merged["sensitive_variant_level"] = "normal"
    return merged


# ---------------------------------------------------------------- 词库读写

MAX_WORDS_CHARS = 5000  # 与面板上限一致


def clean_words(items: list[str]) -> list[str]:
    """去空白、去空项、按大小写不敏感去重（保持原顺序与原始大小写）。"""
    words: list[str] = []
    seen: set[str] = set()
    for item in items:
        w = str(item).strip()
        if w and w.casefold() not in seen:
            seen.add(w.casefold())
            words.append(w)
    return words


def suggest_for_word(row: dict) -> str:
    """根据命中统计给出处理建议（仅提示，不自动改词库）。"""
    cand, released = row["candidates"], row["released"]
    if row["appeal_accepted"] >= 1:
        return "有申诉通过：建议加豁免短语或移除该词"
    if cand >= 5 and released / cand >= 0.8:
        return "AI 复核多数放行：词太宽，建议改为更具体的词或加豁免短语"
    if cand >= 5 and row["fallback"] / cand >= 0.5:
        return "多数未经 AI 复核：请检查 AI 配置"
    return ""


async def get_words() -> list[str]:
    """当前全局敏感词列表。"""
    raw = str(await _kv("sensitive_words") or "")
    return clean_words(raw.split(","))


async def add_words(items: list[str]) -> tuple[list[str], list[str]]:
    """批量添加，返回（新增, 已存在）。超长则抛 ValueError 且不落库。"""
    requested = clean_words(items)
    current = await get_words()
    existing_keys = {w.casefold() for w in current}
    added, existing = [], []
    for w in requested:
        if w.casefold() in existing_keys:
            existing.append(w)
        else:
            added.append(w)
            existing_keys.add(w.casefold())
    merged = current + added
    text = ",".join(merged)
    if len(text) > MAX_WORDS_CHARS:
        raise ValueError(
            f"词库过长（上限 {MAX_WORDS_CHARS} 字），本次未修改；"
            f"当前 {len(current)} 个词，请先移除部分再添加"
        )
    if added:
        await get_store().set_kv("sensitive_words", text)
    return added, existing


async def remove_words(items: list[str]) -> tuple[list[str], list[str]]:
    """批量移除（大小写不敏感），返回（已移除, 不在词库中）。"""
    requested = clean_words(items)
    current = await get_words()
    keys = {w.casefold() for w in requested}
    current_keys = {w.casefold() for w in current}
    removed = [w for w in current if w.casefold() in keys]
    missing = [w for w in requested if w.casefold() not in current_keys]
    if removed:
        await get_store().set_kv(
            "sensitive_words", ",".join(w for w in current if w.casefold() not in keys)
        )
    return removed, missing


# ---------------------------------------------------------------- 匹配

_ASCII_WORD_RE = re.compile(r"^[a-z0-9][a-z0-9 ._+\-]*$")
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_PINYIN_CACHE_MAX = 4096


def _normalize(text: str) -> str:
    """变体归一化：NFKC（全角→半角）→ 繁转简 → 去掉空格/标点/表情，只留字母数字与汉字。"""
    t = unicodedata.normalize("NFKC", text).lower()
    return _KEEP_RE.sub("", _t2s(t))


_pinyin_cache: dict[str, tuple[str, ...]] = {}


def _pinyin(text: str) -> tuple[str, ...]:
    """转为小写音节序列（缓存）；非中文片段整体保留为一个元素。"""
    cached = _pinyin_cache.get(text)
    if cached is None:
        cached = tuple(s.lower() for s in lazy_pinyin(text))
        if len(_pinyin_cache) >= _PINYIN_CACHE_MAX:
            _pinyin_cache.clear()
        _pinyin_cache[text] = cached
    return cached


def _contains_slice(hay: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """音节序列的连续匹配（避免 daili 命中 dailiang 这类跨音节误报）。"""
    n = len(needle)
    if not n or n > len(hay):
        return False
    for i in range(len(hay) - n + 1):
        if hay[i : i + n] == needle:
            return True
    return False


class _WordForm(NamedTuple):
    word: str  # 词库原文
    pattern: re.Pattern[str] | None  # 原样文本上，纯 ASCII 词的边界正则；中文词为 None
    normalized: str  # 归一化形式
    normalized_pattern: re.Pattern[str] | None  # 归一化文本上的边界正则（纯 ASCII 词）
    pinyin: tuple[str, ...]  # 归一化形式的音节序列
    pinyin_join: str  # 音节拼接串，用于「拼音直写」文本的兜底匹配


def split_word_lists(raw: str) -> tuple[list[str], list[str]]:
    """拆分词库为（普通词, 豁免短语）；`~前缀` 为豁免短语。"""
    words: list[str] = []
    exempts: list[str] = []
    for item in str(raw).split(","):
        w = item.strip()
        if not w:
            continue
        if w.startswith("~"):
            phrase = w[1:].strip()
            if phrase:
                exempts.append(phrase)
        else:
            words.append(w)
    return words, exempts


def _build_matchers(words: list[str]) -> list[_WordForm]:
    """为每个词条预编译三种形态：原样（ASCII 词边界 / 中文子串）、归一化、拼音。"""
    forms: list[_WordForm] = []
    for w in words:
        wl = w.lower()
        pattern = None
        if _ASCII_WORD_RE.match(wl):
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(wl) + r"(?![a-z0-9])")
        normalized = _normalize(w)
        normalized_pattern = None
        if normalized and _ASCII_WORD_RE.match(normalized):
            normalized_pattern = re.compile(
                r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])"
            )
        syllables = _pinyin(normalized)
        forms.append(
            _WordForm(
                w, pattern, normalized, normalized_pattern, syllables, "".join(syllables)
            )
        )
    return forms


_matcher_cache: dict[tuple[str, ...], list[_WordForm]] = {}


def _matchers(words: list[str]) -> list[_WordForm]:
    key = tuple(words)
    cached = _matcher_cache.get(key)
    if cached is None:
        if len(_matcher_cache) >= 8:
            _matcher_cache.clear()
        cached = _build_matchers(words)
        _matcher_cache[key] = cached
    return cached


def find_hit(
    text: str, words: list[str], exempts: list[str], level: str = "normal"
) -> str | None:
    """返回命中的词（词库原文）。

    basic：原样子串 / 词边界；normal：再加归一化（全角、空格标点、繁简）；
    pinyin：再加拼音匹配（谐音、拼音写法）。
    """
    hay = text.lower()
    for phrase in exempts:
        p = phrase.lower()
        if p in hay:
            hay = hay.replace(p, " ")
    for form in _matchers(words):
        if form.pattern is not None:
            if form.pattern.search(hay):
                return form.word
        elif form.word.lower() in hay:
            return form.word
    if level == "basic":
        return None

    nhay = _normalize(hay)
    for phrase in exempts:
        p = _normalize(phrase)
        if p and p in nhay:
            nhay = nhay.replace(p, "")
    for form in _matchers(words):
        if form.normalized_pattern is not None:
            if form.normalized_pattern.search(nhay):
                return form.word
        elif form.normalized and form.normalized in nhay:
            return form.word
    if level != "pinyin":
        return None

    syllables = _pinyin(nhay)
    runs = re.findall(r"[a-z0-9]+", nhay)
    for form in _matchers(words):
        # 纯 ASCII 词已在前两轮按词边界匹配过，拼音通道只用于含中文的词
        if form.normalized_pattern is not None or not form.pinyin:
            continue
        if _contains_slice(syllables, form.pinyin):
            return form.word
        # 拼音直写（如 "jiawoweixin"）：在归一化文本的字母数字串里找音节拼接串
        if len(form.pinyin_join) >= 4 and any(form.pinyin_join in run for run in runs):
            return form.word
    return None


# ---------------------------------------------------------------- AI 复核

_REVIEW_PROMPT = (
    "你是 QQ 群内容安全审核助手。下面的群消息命中了敏感词库中的词，"
    "但有些词在正常聊天中也会出现（如「机场」「梯子」「代理」「魔法」等技术讨论）。\n"
    "请判断这条消息是否构成违规推广，只输出一个 JSON 对象（不要 Markdown 代码块）：\n"
    '{"verdict": "violation"|"normal", "category": "vpn"|"ads"|"other"|"none", '
    '"reason": "10字以内中文理由"}\n'
    "判定规则：\n"
    "- 推销/售卖/拉人购买翻墙工具、机场、节点、代理，或发布引流广告"
    "（代刷、代开发票、加微信私聊、低价代充等）→ violation；\n"
    "- 正常技术讨论、提问、经验交流、闲聊、抱怨、玩笑 → normal；\n"
    "- 不确定时判 normal。\n\n"
    "命中的词：[[WORD]]\n消息内容：[[TEXT]]"
)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_REVIEW_CACHE_TTL = 1800  # 同一群同样文本 30 分钟内不重复送审
_REVIEW_CACHE_MAX = 500
_review_cache: dict[tuple[int, str], tuple[float, str, str]] = {}


async def _ai_review(group_id: int, text: str, hit: str) -> tuple[str, str]:
    """AI 复核命中候选，返回 (decision, reason)：violation / safe / unknown。"""
    cache_key = (group_id, text[:200])
    now = time.monotonic()
    cached = _review_cache.get(cache_key)
    if cached and now - cached[0] < _REVIEW_CACHE_TTL:
        return cached[1], cached[2]
    from src.plugins import ai as ai_plugin

    if not await ai_plugin.is_ai_enabled():
        return "unknown", "AI 未开启或未配置"
    try:
        raw = await asyncio.wait_for(
            ai_plugin.generate_text(
                _REVIEW_PROMPT.replace("[[WORD]]", hit).replace("[[TEXT]]", text[:300]),
                max_tokens=300,
                temperature=0.1,
            ),
            timeout=12,
        )
    except TimeoutError:
        return "unknown", "AI 复核超时"
    except Exception:
        logger.exception("AI 复核调用异常")
        return "unknown", "AI 复核异常"
    if not raw:
        return "unknown", "AI 无返回"
    match = _JSON_RE.search(raw)
    if not match:
        return "unknown", "AI 返回格式异常"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "unknown", "AI 返回解析失败"
    verdict = str(data.get("verdict", "")).strip().lower()
    decision = "violation" if verdict == "violation" else "safe"
    reason = str(data.get("reason", "")).strip()[:30]
    if len(_review_cache) >= _REVIEW_CACHE_MAX:
        _review_cache.clear()
    _review_cache[cache_key] = (now, decision, reason)
    return decision, reason


# ---------------------------------------------------------------- 升级处罚

_MAX_MUTE_MINUTES = 30 * 24 * 60  # QQ 禁言上限 30 天
_ESCALATION_BASE_MINUTES = 10  # 未配置基础禁言时，第 2 次起翻倍的基数


def escalation_plan(strike: int, base_minutes: int) -> tuple[str, int]:
    """第 strike 次（从 1 起）违规的处理，返回 (kind, mute_minutes)。

    kind：base=沿用当前规则 / mute=禁言 / kick=踢出群聊
    """
    if strike <= 1:
        return "base", base_minutes
    if strike == 2:
        return "mute", min(max(base_minutes, _ESCALATION_BASE_MINUTES) * 2, _MAX_MUTE_MINUTES)
    if strike == 3:
        return "mute", _MAX_MUTE_MINUTES
    return "kick", 0


def trial_remaining(started_ts: float, now_ts: float, trial_days: int) -> int:
    """试运行还剩几天（向上取整）；0 表示已结束或无试运行。"""
    if trial_days <= 0:
        return 0
    left = trial_days * 86400 - (now_ts - started_ts)
    return max(0, int(-(-left // 86400)))


def parse_ids(raw: str) -> set[int]:
    """解析逗号/空白分隔的 QQ 号，忽略非数字。"""
    return {int(x) for x in re.split(r"[\s,，、;；]+", str(raw or "")) if x.strip().isdigit()}


def kick_decision(
    *,
    role: str | None,
    user_id: int,
    superusers: set[int],
    exempt_ids: set[int],
    kicks_today: int,
    daily_cap: int,
    trial_left_days: int,
) -> tuple[bool, str]:
    """是否允许自动踢出；不允许时返回原因（用于改判最长禁言并告知超管）。"""
    if user_id in superusers:
        return False, "对方是超管"
    if role in ("owner", "admin"):
        return False, "对方是群主/管理员"
    if user_id in exempt_ids:
        return False, "对方在踢人豁免名单中"
    if trial_left_days > 0:
        return False, f"升级处罚试运行期（还剩 {trial_left_days} 天）"
    if daily_cap > 0 and kicks_today >= daily_cap:
        return False, f"本群今日已自动踢出 {kicks_today} 人，达到每日上限"
    return True, ""


async def _trial_left(trial_days: int) -> int:
    """首次用到时记下起点，之后按天数递减。"""
    store = get_store()
    raw = await store.get_kv("sensitive_escalation_started")
    if not raw or not raw.isdigit():
        await store.set_kv("sensitive_escalation_started", str(int(time.time())))
        return trial_days
    return trial_remaining(int(raw), time.time(), trial_days)


# ---------------------------------------------------------------- 处理

sensitive_matcher = on_message(rule=GROUP_WHITELIST, priority=3, block=False)


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def _notify(bot: Bot, text: str) -> None:
    for uid in _superuser_ids():
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"敏感词通知超管 {uid} 失败")


@sensitive_matcher.handle()
async def handle_sensitive(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id):
        return
    cfg = await sensitive_config(event.group_id)
    if not cfg["sensitive_enabled"] or not str(cfg["sensitive_words"]).strip():
        return
    text = event.message.extract_plain_text().strip()
    if not text:
        return

    words, exempts = split_word_lists(str(cfg["sensitive_words"]))
    hit = find_hit(text, words, exempts, cfg["sensitive_variant_level"])
    if not hit:
        return

    # 命中候选先交 AI 结合语境复核（关闭复核时直接按违规处理）
    decision, review_reason = "violation", ""
    if cfg["sensitive_review"]:
        decision, review_reason = await _ai_review(event.group_id, text, hit)
    try:
        await get_store().incr_word_stat(
            hit, {"safe": "released", "violation": "punished"}.get(decision, "fallback")
        )
    except Exception:
        logger.exception("写入敏感词命中统计失败")
    if decision == "safe":
        logger.info(
            f"敏感词候选放行（AI 复核 normal）：group={event.group_id} 词={hit}"
            f" 理由={review_reason}"
        )
        return  # 正常消息，不阻断后续 AI / 关键词回复

    matcher.stop_propagation()  # 撤回或转人工，均不再触发 AI / 关键词回复

    if decision == "unknown" and cfg["sensitive_review_fallback"] == "notify":
        logger.warning(
            f"敏感词候选待人工判断（AI 复核不可用：{review_reason}）："
            f"group={event.group_id} user={event.user_id} 词={hit}"
        )
        if cfg["sensitive_notify"]:
            await _notify(
                bot,
                f"⚠️ 敏感词疑似命中（AI 复核不可用，未自动撤回）\n"
                f"群号: {event.group_id}\nQQ: {event.user_id}\n"
                f"命中词: {hit}\n原因: {review_reason}，请人工判断\n"
                f"内容: {text[:100]}",
            )
        return

    logger.warning(
        f"敏感词命中{'（AI 复核不可用，按兜底策略处理）' if decision == 'unknown' else ''}："
        f"group={event.group_id} user={event.user_id} 词={hit}"
    )

    # 升级处罚：只有确认违规（非兜底撤回）才计数与升级
    counted = decision == "violation"
    kind, mute_minutes = "base", cfg["sensitive_mute_minutes"]
    strike = 0
    if cfg["sensitive_escalation"] and counted:
        try:
            prior = await get_store().count_strikes(
                event.group_id, event.user_id, days=cfg["sensitive_strike_days"]
            )
        except Exception:
            logger.exception("统计违规次数失败，本次按首次处理")
            prior = 0
        strike = prior + 1
        kind, mute_minutes = escalation_plan(strike, cfg["sensitive_mute_minutes"])

    # 撤回
    recalled, recall_err = False, ""
    try:
        await bot.call_api("delete_msg", message_id=event.message_id)
        recalled = True
    except Exception as e:
        recall_err = str(e)
        logger.warning(f"敏感词撤回失败：{e}")

    # 踢出（第 4 次起）——先过防线，不通过则改判最长禁言
    kicked, kick_err, kick_held = False, "", ""
    if kind == "kick":
        try:
            allowed, kick_held = kick_decision(
                role=getattr(event.sender, "role", None),
                user_id=event.user_id,
                superusers=_superuser_ids(),
                exempt_ids=parse_ids(str(cfg["sensitive_exempt_users"])),
                kicks_today=await get_store().count_actions_today(event.group_id, "kick"),
                daily_cap=cfg["sensitive_kick_daily_cap"],
                trial_left_days=await _trial_left(cfg["sensitive_trial_days"]),
            )
        except Exception:
            logger.exception("踢人防线检查失败，保守起见不踢")
            allowed, kick_held = False, "防线检查出错"
        if not allowed:
            kind, mute_minutes = "mute", _MAX_MUTE_MINUTES
    if kind == "kick":
        try:
            await bot.call_api(
                "set_group_kick",
                group_id=event.group_id,
                user_id=event.user_id,
                reject_add_request=False,
            )
            kicked = True
        except Exception as e:
            kick_err = str(e)
            logger.exception("敏感词踢出失败")
            # 踢人失败退化为最长禁言，避免惯犯毫无处理
            kind, mute_minutes = "mute", _MAX_MUTE_MINUTES

    # 禁言（可选）
    muted = False
    if not kicked and mute_minutes > 0:
        try:
            await bot.call_api(
                "set_group_ban",
                group_id=event.group_id,
                user_id=event.user_id,
                duration=min(mute_minutes * 60, 30 * 24 * 3600),
            )
            muted = True
        except Exception:
            logger.exception("敏感词禁言失败")

    # 群内 @ 被处罚者并说明原因，避免莫名其妙被禁言
    if recalled or muted or kicked:
        strike_note = f"（{cfg['sensitive_strike_days']} 天内第 {strike} 次违规）" if strike > 1 else ""
        if kicked:
            reason = f"{event.user_id} 多次发送敏感违禁词{strike_note}，已被移出群聊。"
        elif muted:
            reason = f"你因发送了敏感违禁词被禁言{strike_note}，请记得阅读并遵守群规！"
        else:
            reason = "你因发送了敏感违禁词，消息已被撤回，请记得阅读并遵守群规！"
        try:
            tail = "\n（如认为误判，可私聊机器人直接发送申诉理由，7 天内有效）"
            await bot.call_api(
                "send_group_msg",
                group_id=event.group_id,
                message=(
                    reason
                    if kicked
                    else MessageSegment.at(event.user_id) + " " + reason + tail
                ),
            )
        except Exception:
            logger.exception(f"发送敏感词处罚提示失败：group={event.group_id}")
        # 结构化记录（不存聊天内容），供面板查询与申诉
        if kicked:
            action = "kick"
        else:
            action = "recall_mute" if (recalled and muted) else ("mute" if muted else "recall")
        try:
            await get_store().add_punishment(
                group_id=event.group_id,
                user_id=event.user_id,
                word=hit,
                action=action,
                mute_minutes=mute_minutes if muted else 0,
                reason=review_reason,
                counted=counted,
                message_id=event.message_id,
            )
        except Exception:
            logger.exception("写入违规记录失败")

    if cfg["sensitive_notify"]:
        status = "已撤回" if recalled else f"撤回失败：{recall_err}"
        extra = f"，并已禁言 {mute_minutes} 分钟" if muted else ""
        if kicked:
            extra = "，并已踢出群聊"
        elif kick_err:
            extra += f"（踢出失败：{kick_err}，已改为最长禁言）"
        elif kick_held:
            extra += f"\n⚠️ 按规则本应踢出，但{kick_held}，已改为最长禁言"
        if strike:
            extra += f"\n本群 {cfg['sensitive_strike_days']} 天内第 {strike} 次违规"
        review_line = (
            f"AI 复核: {review_reason}\n"
            if cfg["sensitive_review"] and decision == "violation" and review_reason
            else ""
        )
        await _notify(
            bot,
            f"🚨 敏感词告警\n群号: {event.group_id}\nQQ: {event.user_id}\n"
            f"命中词: {hit}\n处理: {status}{extra}\n"
            f"{review_line}"
            f"内容: {text[:100]}",
        )


# ---------------------------------------------------------------- 指令：/敏感词

sensitive_admin = on_command(
    "敏感词", aliases={"sensitive"}, rule=SUPERUSER, priority=1, block=True
)

_USAGE = (
    "用法：\n"
    "/敏感词 add <词1> [词2] … — 批量添加（空格 / 逗号分隔）\n"
    "/敏感词 del <词1> [词2] … — 批量移除\n"
    "/敏感词 list — 查看词库\n"
    "/敏感词 豁免 <QQ…> | 取消豁免 <QQ…> | 豁免列表 — 管理「不会被自动踢出」的名单\n"
    "词条以 ~ 开头为豁免短语（如 ~机场大巴），正常讨论不被误伤"
)
_ADD_ACTIONS = {"add", "添加", "增加"}
_DEL_ACTIONS = {"del", "delete", "remove", "删除", "移除"}
_LIST_ACTIONS = {"list", "列表", "查看"}
_EXEMPT_ADD = {"豁免", "exempt"}
_EXEMPT_DEL = {"取消豁免", "unexempt"}
_EXEMPT_LIST = {"豁免列表"}


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


def _brief(words: list[str], limit: int = 600) -> str:
    """把词列表拼成一行，过长时截断（防止 QQ 消息刷屏）。"""
    text = ""
    for w in words:
        piece = w if not text else "、" + w
        if len(text) + len(piece) > limit:
            return f"{text}……（共 {len(words)} 个）"
        text += piece
    return text


@sensitive_admin.handle()
async def handle_sensitive_admin(args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    words = clean_words(re.split(r"[\s,，、;；]+", rest))

    if action in _LIST_ACTIONS or not action:
        current = await get_words()
        cfg = await sensitive_config()
        state = "开启" if cfg["sensitive_enabled"] else "关闭"
        if not current:
            await _send(sensitive_admin, f"敏感词词库为空（监控开关：{state}）。\n{_USAGE}")
            return
        await _send(
            sensitive_admin,
            f"敏感词词库（全局，共 {len(current)} 个，监控：{state}）：\n{_brief(current)}",
        )
        return

    if action in _EXEMPT_LIST:
        ids = sorted(parse_ids(str(await _kv("sensitive_exempt_users"))))
        await _send(sensitive_admin, f"踢人豁免名单（{len(ids)} 人）：{'、'.join(map(str, ids)) or '空'}")
        return
    if action in _EXEMPT_ADD | _EXEMPT_DEL:
        targets = parse_ids(rest)
        if not targets:
            await _send(sensitive_admin, "请在指令后写 QQ 号，如：/敏感词 豁免 10001 10002")
            return
        current = parse_ids(str(await _kv("sensitive_exempt_users")))
        current = current | targets if action in _EXEMPT_ADD else current - targets
        await get_store().set_kv("sensitive_exempt_users", ",".join(map(str, sorted(current))))
        verb = "加入" if action in _EXEMPT_ADD else "移出"
        await _send(sensitive_admin, f"已{verb} {len(targets)} 人，当前豁免名单共 {len(current)} 人。")
        return

    if action in _ADD_ACTIONS:
        if not words:
            await _send(sensitive_admin, _USAGE)
            return
        try:
            added, existing = await add_words(words)
        except ValueError as e:
            await _send(sensitive_admin, f"添加失败：{e}。")
            return
        total = len(await get_words())
        lines = []
        if added:
            lines.append(f"已添加 {len(added)} 个：{_brief(added)}")
        if existing:
            lines.append(f"已存在 {len(existing)} 个（跳过）：{_brief(existing)}")
        lines.append(f"词库共 {total} 个。")
        if added and not (await sensitive_config())["sensitive_enabled"]:
            lines.append("⚠️ 敏感词监控当前未开启，词库暂不生效（可在面板「敏感词」页开启）。")
        await _send(sensitive_admin, "\n".join(lines))
        return

    if action in _DEL_ACTIONS:
        if not words:
            await _send(sensitive_admin, _USAGE)
            return
        removed, missing = await remove_words(words)
        total = len(await get_words())
        lines = []
        if removed:
            lines.append(f"已移除 {len(removed)} 个：{_brief(removed)}")
        if missing:
            lines.append(f"不在词库中 {len(missing)} 个：{_brief(missing)}")
        lines.append(f"词库共 {total} 个。")
        await _send(sensitive_admin, "\n".join(lines))
        return

    await _send(sensitive_admin, _USAGE)


# ---------------------------------------------------------------- 申诉（私聊 / 群临时会话）
#
# 被处罚者多数不是机器人好友，只能通过「群临时会话」私聊；且很少会按格式写 `/申诉`。
# 所以：1) 任何非超管私聊消息，只要近期有可申诉的处罚，就整条当作申诉理由；
#      2) 回复一律带上被处罚的群号走临时会话通道（失败再退回普通私聊）；
#      3) 记录每条非超管私聊，便于排查「机器人没反应」。

APPEAL_WINDOW_HOURS = 24 * 7  # 处罚后 7 天内可申诉
_HINT_COOLDOWN = 6 * 3600  # 同一人的非申诉提示最多每 6 小时一次，防刷屏/与其他机器人对聊
_hint_sent: dict[int, float] = {}


async def _is_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


async def _is_private_non_super(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and event.user_id not in _superuser_ids()


async def send_private(
    bot: Bot, user_id: int, text: str, group_id: int | None = None
) -> bool:
    """发私聊；给出 group_id 时先走群临时会话（非好友必需），失败退回普通私聊。"""
    attempts: list[dict[str, Any]] = []
    if group_id:
        attempts.append({"user_id": user_id, "group_id": group_id})
    attempts.append({"user_id": user_id})
    last: Exception | None = None
    for params in attempts:
        try:
            await bot.call_api("send_private_msg", message=text, **params)
            return True
        except Exception as e:
            last = e
    logger.warning(f"私聊发送失败：user={user_id} group={group_id} {type(last).__name__}: {last}")
    return False


def _hint_allowed(user_id: int) -> bool:
    now = time.monotonic()
    if now - _hint_sent.get(user_id, -_HINT_COOLDOWN) < _HINT_COOLDOWN:
        return False
    if len(_hint_sent) > 2000:
        _hint_sent.clear()
    _hint_sent[user_id] = now
    return True


async def _handle_appeal(bot: Bot, event: PrivateMessageEvent, text: str) -> None:
    """处理一条申诉文本：落库、回复申诉人、通知超管。"""
    store = get_store()
    latest = await store.latest_punishment_for(event.user_id, hours=APPEAL_WINDOW_HOURS)
    group_id = (latest or {}).get("group_id") or getattr(event, "group_id", None)

    async def reply(msg: str) -> None:
        if not await send_private(bot, event.user_id, msg, group_id) and latest is not None:
            await _notify(bot, f"⚠️ 无法回复申诉人 QQ {event.user_id}（临时会话发送失败），请手动联系。")

    if latest is None:
        if _hint_allowed(event.user_id):
            await reply(
                "我是群助手机器人。近 7 天内没有查到你的违规记录，"
                "如有疑问请在群里联系管理员。若被处罚后想申诉，请直接发送你的申诉理由。"
            )
        return
    if latest["appeal_status"] != "none":
        if _hint_allowed(event.user_id):
            status = {"pending": "正在等待管理员处理", "accepted": "已通过", "rejected": "已被驳回"}.get(
                latest["appeal_status"], "已处理"
            )
            await reply(f"你最近一次处罚的申诉{status}，请勿重复提交。")
        return
    if not await store.appeal_punishment(latest["id"], text):
        await reply("申诉提交失败，请稍后重试或联系管理员。")
        return
    await reply("已收到你的申诉，管理员会尽快复核，处理结果会通过机器人通知你，请留意消息。")
    from src.plugins import appeals

    full = await store.get_punishment(latest["id"])
    await _notify(bot, await appeals.format_notice(full or {**latest, "user_id": event.user_id,
                                                             "reason": "", "source": "sensitive"}, text))


appeal_cmd = on_command(
    "申诉", aliases={"appeal"}, rule=Rule(_is_private_non_super), priority=1, block=True
)
# 兜底：没写 /申诉 的普通私聊（含群临时会话）也当作申诉；不阻断超管与其他功能
appeal_free = on_message(rule=Rule(_is_private_non_super), priority=2, block=False)


@appeal_cmd.handle()
async def handle_appeal_cmd(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    logger.info(f"收到私聊申诉指令：user={event.user_id} sub_type={event.sub_type}")
    if not text:
        await send_private(
            bot, event.user_id, "请在 /申诉 后面写上你的理由，例如：/申诉 我只是在讨论技术，没有发广告",
            getattr(event, "group_id", None),
        )
        return
    await _handle_appeal(bot, event, text)


@appeal_free.handle()
async def handle_appeal_free(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
    text = event.message.extract_plain_text().strip()
    logger.info(
        f"收到非超管私聊：user={event.user_id} sub_type={event.sub_type}"
        f" 内容前 30 字={text[:30]!r}"
    )
    matcher.stop_propagation()  # 非超管私聊没有其他功能，不再触发 AI / 回复等后续处理
    if not text:
        return  # 纯图片/表情/语音等不当作申诉理由
    await _handle_appeal(bot, event, text)
