"""敏感词匹配引擎（纯函数，无 IO）：敏感词、话题规约、生图提示词检查共用。

- 词表：逗号分隔，`~前缀` 为豁免短语（匹配前从文本中剔除，避免「机场大巴」误伤「机场」）
- 纯英文/数字词条按词边界匹配（PIA 不会命中 Olympiad）
- level：basic=原样；normal=归一化（全角/空格标点/繁简）；
  pinyin=归一化+拼音（拦「加 微 信」「ＶＰＮ」「廣告」「weixin/威信」等绕过写法）
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

from opencc import OpenCC
from pypinyin import lazy_pinyin

try:
    _t2s = OpenCC("t2s").convert
except Exception:  # 词典缺失等异常时退化为不做繁简转换

    def _t2s(text: str) -> str:
        return text


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
