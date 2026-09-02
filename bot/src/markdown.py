"""Markdown → QQ 纯文本降级转换（md_to_qq）。

背景：QQ 群聊（含 NapCat 通道）不渲染标准 Markdown，LLM 返回的
`**加粗**`、`# 标题`、`[链接](url)`、代码块等会以「源码」原样发出，
观感很差。所有 AI 产出的文本在发送前必须经过本模块转换。

转换原则（有损降级，追求可读）：
- 标题           → 「【标题】」
- 加粗/斜体/删除线 → 去掉标记，保留文字
- 行内代码        → 去掉反引号
- 围栏代码块      → 去掉 ```，内容原样保留（缩进两格增强可读性）
- 链接 [t](u)    → t（u）
- 图片 ![a](u)   → [图片]
- 引用           → 行首「▎」
- 无序列表        → 「•」
- 表格           → 去掉分隔行，竖线改全角「｜」
- 水平线         → 「────────」
- 多余空行       → 压缩为一个空行

规约：新增任何「AI 生成文本直接发送」的出口，都必须先调用 md_to_qq。
详见 docs/AI_CAPABILITIES.md。
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```[^\S\n]*[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<![\w_])_([^_\n]+)_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?")
_ULIST_RE = re.compile(r"^(\s*)[-*+]\s+")
_TABLE_SEP_RE = re.compile(r"^\s{0,3}\|?\s*:?-{3,}.*\|\s*$")
_PIPE_RE = re.compile(r"\|")
_LEADING_PIPE_RE = re.compile(r"^\s*\|")
_TRAILING_PIPE_RE = re.compile(r"\|\s*$")


def _convert_table_row(line: str) -> str:
    line = _LEADING_PIPE_RE.sub("", line)
    line = _TRAILING_PIPE_RE.sub("", line)
    line = _PIPE_RE.sub("｜", line)
    return line.strip()


def md_to_qq(text: str) -> str:
    """把 LLM 输出的 Markdown 降级为 QQ 可读的纯文本。幂等、无异常抛出。"""
    if not text:
        return text
    try:
        return _convert(text)
    except Exception:  # 转换失败绝不影响发送，退回原文
        return text


def _convert(text: str) -> str:
    # 1) 围栏代码块：去 ```，内容整体缩进两格
    def _fence(m: re.Match) -> str:
        code = m.group(1).rstrip("\n")
        lines = [("  " + ln if ln.strip() else "") for ln in code.split("\n")]
        return "\n".join(lines)

    text = _FENCE_RE.sub(_fence, text)

    out_lines: list[str] = []
    in_table = False
    for line in text.split("\n"):
        # 表格：跳过分隔行（| --- | --- |）
        if _TABLE_SEP_RE.match(line):
            in_table = True
            continue
        if line.lstrip().startswith("|"):
            line = _convert_table_row(line)
            in_table = True
        elif in_table and line.strip():
            in_table = False

        # 水平线
        if _HR_RE.match(line):
            out_lines.append("────────")
            continue
        # 标题
        m = _HEADING_RE.match(line)
        if m:
            out_lines.append(f"【{m.group(1).strip()}】")
            continue
        # 引用
        line = _QUOTE_RE.sub("▎", line)
        # 无序列表符号
        line = _ULIST_RE.sub(r"\1• ", line)
        out_lines.append(line)
    text = "\n".join(out_lines)

    # 2) 行内元素
    text = _IMAGE_RE.sub(lambda m: "[图片]", text)
    text = _LINK_RE.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", text)
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)

    # 3) 压缩连续空行、去首尾空白
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
