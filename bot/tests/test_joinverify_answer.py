"""入群验证的答案校验：必须只认「报的那个数」，不能靠算式蒙混。"""

import pytest

from src.plugins.joinverify import (
    check_answer,
    is_wrong_attempt,
    looks_like_expression,
    normalize_answer,
)


@pytest.mark.parametrize("text,answer", [
    ("9", 9),
    ("答案是 9", 9),
    ("答案是 9 哦", 9),
    ("我算出来是 9", 9),
    ("等于9", 9),
    ("回答：9", 9),
    ("我的答案是9", 9),
    ("9！", 9),          # 全角感叹号是语气，不是阶乘
    ("  9  ", 9),
    ("０", 0),           # 全角数字
    ("18", 18),
])
def test_accepts_plain_answers(text, answer):
    assert check_answer(text, answer) is True


@pytest.mark.parametrize("text", [
    "e^2",              # 指数
    "1e9",              # 科学计数法
    "e^2.197",
    "3^2",
    "2^3+1",
    "1+2+3+4+5+6+7+8+9",  # 长算式，中间刚好出现 9
    "ln(e)+8",
    "sqrt(81)",
    "9.0",              # 小数
    "0x9",              # 十六进制
    "9的平方根",          # 文字表达式（结果其实是 3）
    "9/1",
    "9*1",
    "9+0",
    "根号81",
])
def test_rejects_expressions(text):
    """这些以前全都能通过（只要文本里有个 9）。"""
    assert check_answer(text, 9) is False


def test_rejects_wrong_number():
    assert check_answer("12", 9) is False
    assert check_answer("111", 11) is False   # 不能把 111 里的 11 当成答案
    assert check_answer("你好", 9) is False
    assert check_answer("", 9) is False


def test_normalize_strips_filler_and_punct():
    assert normalize_answer("答案是 11 哦") == "11"
    assert normalize_answer("ｅ＾２") == "e^2"      # 全角归一化，算式仍然保留
    assert normalize_answer("回答：9！") == "9"


def test_looks_like_expression():
    for text in ("e^2", "1e9", "3^2", "9.0", "0x9", "9的平方根", "sqrt(81)"):
        assert looks_like_expression(text) is True, text
    for text in ("9", "答案是 9", "9！"):
        assert looks_like_expression(text) is False, text


def test_wrong_attempt_counts_expressions():
    """算式糊弄要计入答错次数，否则可以无限重试。"""
    assert is_wrong_attempt("1+2+3+4+5+6+7+8+9", 9) is True
    assert is_wrong_attempt("e^2", 9) is True
    # 纯聊天没有数字，不算答错
    assert is_wrong_attempt("你好呀", 9) is False
    assert is_wrong_attempt("", 9) is False
    # 答对了就不算答错
    assert is_wrong_attempt("9", 9) is False


def test_hint_tells_expression_users_to_just_send_number():
    from src.plugins.joinverify import wrong_answer_hint

    assert "不用算" in wrong_answer_hint("9-9", 2, "1+2+3+4+5+6+7+8+9")
    assert "直接回复" in wrong_answer_hint("9-9", 2, "e^2")
    # 普通答错保持原样
    plain = wrong_answer_hint("3+8", 2, "12")
    assert "答案不对" in plain and "不用算" not in plain
