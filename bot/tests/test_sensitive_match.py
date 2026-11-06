from src.plugins.sensitive import clean_words, find_hit, split_word_lists, suggest_for_word


def test_split_word_lists_separates_exempt_phrases():
    words, exempts = split_word_lists("机场, 梯子,~机场大巴, ,PIA")
    assert words == ["机场", "梯子", "PIA"]
    assert exempts == ["机场大巴"]


def test_basic_substring_hit():
    assert find_hit("卖机场节点", ["机场"], [], "basic") == "机场"
    assert find_hit("今天天气好", ["机场"], [], "basic") is None


def test_exempt_phrase_prevents_false_positive():
    assert find_hit("我坐机场大巴去", ["机场"], ["机场大巴"]) is None
    assert find_hit("我坐机场大巴去，顺便买机场", ["机场"], ["机场大巴"]) == "机场"


def test_ascii_words_use_word_boundaries():
    assert find_hit("I love Olympiad", ["PIA"], []) is None
    assert find_hit("buy PIA now", ["PIA"], []) == "PIA"


def test_normalized_level_catches_spacing_and_fullwidth():
    assert find_hit("加 微 信", ["加微信"], [], "basic") is None
    assert find_hit("加 微 信", ["加微信"], [], "normal") == "加微信"
    assert find_hit("ＶＰＮ 出售", ["VPN"], [], "normal") == "VPN"


def test_pinyin_level_catches_homophones_only_when_enabled():
    assert find_hit("加威信", ["加微信"], [], "normal") is None
    assert find_hit("加威信", ["加微信"], [], "pinyin") == "加微信"


def test_traditional_chinese_is_normalized():
    assert find_hit("廣告代發", ["广告"], [], "normal") == "广告"


def test_clean_words_dedupes_case_insensitively():
    assert clean_words([" VPN", "vpn", "", "机场", "机场 "]) == ["VPN", "机场"]


def _row(**over):
    base = dict(candidates=0, released=0, punished=0, fallback=0, appeal_accepted=0, appeals=0)
    base.update(over)
    return base


def test_word_suggestions():
    assert suggest_for_word(_row()) == ""
    assert "申诉通过" in suggest_for_word(_row(candidates=3, appeal_accepted=1))
    assert "放行" in suggest_for_word(_row(candidates=10, released=9, punished=1))
    assert "AI" in suggest_for_word(_row(candidates=10, fallback=8, punished=2))
    # 样本太少不下结论
    assert suggest_for_word(_row(candidates=3, released=3)) == ""
