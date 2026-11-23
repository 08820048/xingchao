"""判断一段回复适不适合用语音念出来。

语音适合口语化、简短、听一遍就懂的内容（闲聊、安慰、玩笑、简单回答）。
这些内容念出来很糟或者没法用：链接/代码/指令（要复制）、带编号的步骤和清单（要回头看）、
大段数字和英文（TTS 念不对）、很长的回答（听着累）。不适合时 AI 回复自动退回文字。

纯规则、无外部依赖；返回不适合的原因（给日志用），适合返回 None。
"""

from __future__ import annotations

import re

AUTO_MAX_CHARS = 150  # 默认语音回复的长度上限（用户明确要求发语音时仍可到 300）

_URL_RE = re.compile(r"https?://|www\.|[\w-]+\.(?:com|cn|net|org|io|dev|me|top|cc|xyz)\b|[\w.+-]+@[\w-]+\.\w+", re.I)
_CODE_RE = re.compile(r"```|`[^`\n]+`")
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•·]|\d{1,2}[.、)）]|[（(]\d{1,2}[)）]|[一二三四五六七八九十][、.])\s*\S")
_LONG_DIGITS_RE = re.compile(r"\d{6,}")
_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_FORMAT_RE = re.compile(r"(?<![\w/])/[一-鿿A-Za-z]|[A-Za-z]:\\|(?:/[\w.-]+){2,}|[{}]|\|.*\||^#{1,6}\s", re.M)


def unfit_reason(text: str, *, limit: int = AUTO_MAX_CHARS) -> str | None:
    """返回不适合语音的原因；适合则返回 None。"""
    text = (text or "").strip()
    if len(text) > limit:
        return f"太长（{len(text)} 字）"
    if _CODE_RE.search(text):
        return "含代码"
    if _URL_RE.search(text):
        return "含链接或邮箱"
    if _FORMAT_RE.search(text):
        return "含指令、路径或表格等格式化内容"
    if not _CJK_RE.search(text):
        return "没有可念的中文文字"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if sum(1 for ln in lines if _LIST_LINE_RE.match(ln)) >= 2:
        return "是条目或步骤清单"
    if len(lines) >= 4:
        return "分段很多，更适合看"
    if _LONG_DIGITS_RE.search(text) or len(re.findall(r"\d", text)) >= 12:
        return "含较多数字"
    latin, cjk = len(_LATIN_RE.findall(text)), len(_CJK_RE.findall(text))
    if latin >= 12 and latin > cjk * 0.5:
        return "英文较多"
    visible = len(re.sub(r"\s", "", text))
    if cjk < visible * 0.4:
        return "符号或非文字内容较多"
    return None
