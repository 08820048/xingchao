"""中文/英文检索分词：博客知识库与群聊检索共用。

中文按 2-4 字 n-gram 生成词项，先按语气助词与停用词切分，避免「是什么」「怎么样」
这类跨词碎片进入索引；英文按单词（≥2 字符）取词。两处共用一份停用词表，
以后调整只改这里，避免博客与群聊检索的口径漂移。
"""

from __future__ import annotations

import re

_ASCII_TERM_RE = re.compile(r"[a-z][a-z0-9+#._-]{1,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_FILLER_RE = re.compile(r"[啊呀吧呢吗哦噢嗯诶唉嘛嘞]+")

# 停用词：疑问词 / 口语 / 泛词，避免「怎么、如何、兄弟们」这类词误命中
STOPWORDS = frozenset({
    "如何", "怎么", "怎样", "什么", "为什么", "为何", "哪里", "哪个", "哪些", "是否",
    "有没有", "能不能", "可不可以", "请问", "请教", "求助", "求解", "推荐", "一个",
    "一下", "这个", "那个", "这些", "那些", "我们", "你们", "他们", "自己", "现在",
    "今天", "明天", "昨天", "已经", "就是", "不是", "还是", "但是", "因为", "所以",
    "如果", "然后", "感觉", "觉得", "知道", "不会", "不能", "需要", "问题", "东西",
    "时候", "可以", "应该", "可能", "真的", "兄弟们", "兄弟", "大佬", "老哥", "谢谢",
    "不错", "好用", "怎么办", "咋办", "咋整", "有点", "其实", "大家", "各位", "没有",
    "the", "and", "for", "with", "this", "that", "you", "are", "how", "what",
})

# 中文停用词（长词优先）用于切分，避免「是什么/什么东西」这类跨词碎片进入索引
_CJK_STOP_SPLIT_RE = re.compile(
    "|".join(map(re.escape, sorted((w for w in STOPWORDS if not w.isascii()), key=len, reverse=True)))
)


def terms(text: str) -> set[str]:
    """提取可匹配词：英文单词（≥2） + 中文 2-4 字 n-gram。"""
    lowered = (text or "").lower()
    out: set[str] = set()
    for m in _ASCII_TERM_RE.finditer(lowered):
        word = m.group(0).strip("._+#-")
        if len(word) >= 2 and word not in STOPWORDS:
            out.add(word)
    for m in _CJK_RUN_RE.finditer(lowered):
        for chunk in _CJK_FILLER_RE.split(m.group(0)):
            for run in _CJK_STOP_SPLIT_RE.split(chunk):
                for n in (2, 3, 4):
                    if len(run) < n:
                        continue
                    for i in range(len(run) - n + 1):
                        term = run[i:i + n]
                        if term not in STOPWORDS:
                            out.add(term)
    return out


def term_weight(term: str) -> float:
    """词项权重：英文按长度（封顶 6），中文按字数 ×1.4（长词更具体、更有区分度）。"""
    if term.isascii():
        return min(len(term), 6) * 0.9
    return len(term) * 1.4
