"""广告招商与群内广告识别。"""

from types import SimpleNamespace

import pytest

from src.plugins import ads


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, store):
    monkeypatch.setattr(ads, "get_store", lambda: store)
    ads._reset_state()
    yield
    ads._reset_state()

    # ---------------------------------------------------------------- 识别


@pytest.mark.parametrize(
    "text",
    [
        "接推广，有意私聊，日赚500不是梦",
        "代发广告 一手货源 欢迎来聊",
        "招代理 低门槛 加我微信详聊",
        "低价代充会员，秒到账，加V",
        "推广位招租，带量效果看主页",
    ],
)
def test_strong_ads_are_detected(text):
    assert ads.classify_ad(text)[0] == "ad"


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错，出去走走",
        "这个需求我下午看看，晚点回你",
        "Rust 的 borrow checker 真的好难",
        "刚吃完饭，你们呢",
        "这个优惠力度挺大的",  # 单个营销词不该判广告
        "我在看广告行业的报告",
    ],
)
def test_normal_chat_is_not_ad(text):
    assert ads.classify_ad(text)[0] == "none"


def test_consult_is_guided_not_recalled():
    for text in ("能发广告吗", "想问下广告怎么收费", "推广报价多少", "可以在这里投放广告吗",
                 "广告费一般多少钱啊", "广告位多少钱"):
        assert ads.classify_ad(text)[0] == "consult", text


def test_metatalk_about_ads_is_not_ad():
    assert ads.classify_ad("又在发广告，举报了")[0] == "none"
    assert ads.classify_ad("广告真多，禁言吧")[0] == "none"


def test_quoted_reply_needs_stronger_evidence():
    text = "推广 引流 优惠 私聊"
    assert ads.classify_ad(text)[0] == "ad"
    assert ads.classify_ad(text, replied=True)[0] == "none"


def test_question_form_is_not_ad_without_link():
    assert ads.classify_ad("推广 引流 优惠 私聊？")[0] == "none"
    # 带强特征词时，问句也照样判广告
    assert ads.classify_ad("接推广吗，私聊我")[0] == "ad"


def test_empty_and_short_text():
    assert ads.classify_ad("")[0] == "none"
    assert ads.classify_ad("哈")[0] == "none"


# ---------------------------------------------------------------- 配置


def test_parse_partners_accepts_many_shapes():
    assert ads.parse_partners("") == []
    assert ads.parse_partners("[123, 456]") == [123, 456]
    assert ads.parse_partners("123,456 789") == [123, 456, 789]
    assert ads.parse_partners("１２３") == [123]  # 全角数字
    assert ads.parse_partners(["123", "abc", 0, -5, 123]) == [123]


def test_welcome_line_carries_owner_copy():
    line = ads.welcome_line()
    assert "如果你认为你的产品有价值" in line
    assert "广告报价" in line
    assert "12345" in ads.welcome_line("12345")


async def test_config_defaults_and_roundtrip(store):
    cfg = await ads.ad_config()
    assert cfg["ad_enabled"] is True
    assert cfg["ad_action"] == "reply"
    assert cfg["ad_contact"] == "1"  # 兜底用首个超管
    assert cfg["ad_partners"] == []

    await store.set_kv("ad_action", "silent")
    await store.set_kv("ad_cooldown", "30")
    await store.set_kv("ad_contact", "99999")
    await ads.save_partners([111, 222])
    cfg = await ads.ad_config()
    assert cfg["ad_action"] == "silent"
    assert cfg["ad_cooldown"] == 30
    assert cfg["ad_contact"] == "99999"
    assert cfg["ad_partners"] == [111, 222]
    assert "99999" in await ads.ad_contact_line()


async def test_bad_kv_falls_back_to_default(store):
    await store.set_kv("ad_cooldown", "not-a-number")
    assert (await ads.ad_config())["ad_cooldown"] == ads.DEFAULTS["ad_cooldown"]


async def test_notice_contains_price_and_contact(store):
    await store.set_kv("ad_contact", "88888")
    text = await ads.ad_notice()
    assert "88888" in text and "报价" in text


# ---------------------------------------------------------------- 群内处理


class FakeBot:
    self_id = "9"

    def __init__(self, recall_fails: bool = False):
        self.calls = []
        self.recall_fails = recall_fails

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if name == "delete_msg" and self.recall_fails:
            raise RuntimeError("not admin")


class FakeMatcher:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


def _event(text, uid=10001, gid=555, reply=None, role="member"):
    return SimpleNamespace(
        user_id=uid,
        group_id=gid,
        message_id=4242,
        reply=reply,
        sender=SimpleNamespace(card="小明", role=role),
        get_plaintext=lambda: text,
        get_user_id=lambda: str(uid),
    )


async def _run(text, **kw):
    bot = kw.pop("bot", None) or FakeBot()
    matcher = FakeMatcher()
    await ads.handle_group_ad(bot, _event(text, **kw), matcher)
    return bot, matcher


async def test_handler_recalls_and_prompts():
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert [n for n, _ in bot.calls].count("delete_msg") == 1
    assert any(n == "send_private_msg" for n, _ in bot.calls)  # 通知超管
    assert len(matcher.sent) == 1
    assert "广告报价" in str(matcher.sent[0])


async def test_handler_skips_partners():
    await ads.save_partners([10001])
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert bot.calls == [] and matcher.sent == []


async def test_handler_skips_admins(monkeypatch):
    monkeypatch.setattr(ads, "_is_admin", lambda e: True)
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert bot.calls == [] and matcher.sent == []


async def test_handler_scope_silent():
    await ads.get_store().set_kv("ad_action", "silent")
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert [n for n, _ in bot.calls].count("delete_msg") == 1
    assert matcher.sent == []


async def test_handler_scope_log_keeps_message():
    await ads.get_store().set_kv("ad_action", "log")
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert "delete_msg" not in [n for n, _ in bot.calls]
    assert matcher.sent == []
    assert any(n == "send_private_msg" for n, _ in bot.calls)


async def test_prompt_cooldown_suppresses_second_prompt():
    await _run("接推广 日赚500 加我微信")
    bot, matcher = await _run("代发广告 一手货源")
    assert [n for n, _ in bot.calls].count("delete_msg") == 1
    assert matcher.sent == []  # 冷却内只撤回不刷提示


async def test_consult_gets_notice_without_recall():
    bot, matcher = await _run("能发广告吗")
    assert "delete_msg" not in [n for n, _ in bot.calls]
    assert len(matcher.sent) == 1
    assert "报价" in str(matcher.sent[0])


async def test_recall_failure_is_tolerated():
    bot, matcher = await _run("接推广 日赚500 加我微信", bot=FakeBot(recall_fails=True))
    assert len(matcher.sent) == 1  # 撤回失败仍然提示


async def test_disabled_ads_do_nothing():
    await ads.get_store().set_kv("ad_enabled", "false")
    bot, matcher = await _run("接推广 日赚500 加我微信")
    assert bot.calls == [] and matcher.sent == []


# ---------------------------------------------------------------- 欢迎语接入


def test_default_welcome_embeds_ad_placeholder():
    from src.plugins import groupadmin

    assert "{ad}" in groupadmin.DEFAULT_WELCOME_TEXT
    assert "{ad}" in groupadmin.WELCOME_PLACEHOLDERS


def test_welcome_render_expands_ad_line():
    from src.plugins.groupadmin import render_welcome

    text = render_welcome("你好{ad}", 123, 456, ad_line="广告请联系管理员 1").extract_plain_text()
    assert "广告请联系管理员 1" in text
    assert "{ad}" not in text
    # 占位符缺省（没传 ad_line）时只是被替换成空串，不会把 {ad} 露给新人
    assert "{ad}" not in render_welcome("你好{ad}", 1, 2).extract_plain_text()
