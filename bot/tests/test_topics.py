"""话题规约：机器人不参与的话题（翻墙类 / 涉政），以及不误伤正常讨论。"""

from types import SimpleNamespace

import pytest

from src import llm
from src.plugins import topics


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    monkeypatch.setattr(topics, "get_store", lambda: store)
    yield


# ---------------------------------------------------------------- 翻墙类

@pytest.mark.parametrize("text", [
    "有没有好用的翻墙工具",
    "求个 vpn",
    "科学上网怎么弄",
    "这个机场节点稳定吗",
    "帮我搭个 v2ray",
    "clash 配置怎么写",
])
async def test_proxy_topics_are_blocked(text):
    assert await topics.classify_async(text) is not None


@pytest.mark.parametrize("text", [
    "我坐机场大巴去机场",
    "机场快线几点发车",
    "楼梯的梯子坏了",
    "我们公司买了一台网络加速器",
    "k8s 的节点池扩容了",
    "这个机场建设费多少钱",
    "服务器节点挂了一个",
])
async def test_normal_discussions_are_not_blocked(text):
    assert await topics.classify_async(text) is None


async def test_exempt_phrase_can_be_configured(store):
    await store.set_kv("topics_proxy_words", "机场,~机场大巴")
    assert await topics.classify_async("机场大巴") is None
    assert await topics.classify_async("那个机场能用吗") is not None


# ---------------------------------------------------------------- 涉政

async def test_politics_default_word_list_is_empty_by_design(store):
    """涉政词表默认留空：具体表述因群而异，交给管理员填，程序不硬编码。"""
    assert str(await topics.words_for("politics")) == ""
    assert await topics.classify_async("随便聊聊") is None


async def test_politics_can_be_configured(store):
    await store.set_kv("topics_politics_words", "某敏感表述,~某敏感表述研究")
    assert await topics.classify_async("聊到某敏感表述") is not None
    assert await topics.classify_async("某敏感表述研究") is None   # 豁免短语生效


# ---------------------------------------------------------------- 开关

async def test_master_switch_off_disables_everything(store):
    assert await topics.classify_async("翻墙") is not None
    await store.set_kv("topics_enabled", "false")
    assert await topics.classify_async("翻墙") is None
    assert await topics.should_stay_out("翻墙") is False


async def test_per_category_switch(store):
    await store.set_kv("topics_proxy_enabled", "false")
    assert await topics.classify_async("求个 vpn") is None
    # 关掉一类不影响另一类
    await store.set_kv("topics_politics_words", "某表述")
    assert await topics.classify_async("聊某表述") is not None


# ---------------------------------------------------------------- 话术

async def test_refusal_returns_category_reply(store):
    text = await topics.refusal("有没有好用的翻墙工具")
    assert text is not None
    assert "不参与" in text or "没法讨论" in text


async def test_refusal_is_none_when_not_hit():
    assert await topics.refusal("今天天气不错") is None


async def test_custom_reply_is_used(store):
    await store.set_kv("topics_proxy_reply", "这个我不聊哈")
    assert await topics.refusal("求个 vpn") == "这个我不聊哈"


async def test_empty_kv_falls_back_to_default(store):
    """kv 存空串时应回落到默认词表，否则关掉开关再开就丢词了。"""
    await store.set_kv("topics_proxy_words", "")
    assert "vpn" in str(await topics.words_for("proxy")).lower()
    assert await topics.classify_async("求个 vpn") is not None


# ---------------------------------------------------------------- 提示词底线

def test_prompt_baseline_covers_both_categories():
    text = topics.prompt_baseline()
    assert "翻墙" in text and "涉政" in text
    assert "不参与、不评价" in text
    assert "不要输出相关关键词" in text


def test_short_text_is_ignored():
    assert topics.classify("") is None
    assert topics.classify("嗯") is None


# ---------------------------------------------------------------- 与 AI 对话串联

async def test_chat_returns_refusal_without_calling_model(store, monkeypatch):
    """命中规约时 chat() 直接返回话术，模型一次都不该被调用。"""
    from src.plugins import ai as ai_plugin

    monkeypatch.setattr(ai_plugin, "get_store", lambda: store)

    monkeypatch.setattr(llm, "get_store", lambda: store)
    monkeypatch.setattr(topics, "get_store", lambda: store)

    called: list[dict] = []

    class FakeCompletions:
        @staticmethod
        async def create(**kw):
            called.append(kw)
            raise AssertionError("命中规约时不应调用模型")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions})

    async def fake_client(kind: str = "text"):
        return FakeClient()

    monkeypatch.setattr(llm, "get_client", fake_client)

    async def fake_cfg(raw: bool = False):
        return dict(llm.DEFAULTS)

    monkeypatch.setattr(llm, "ai_config", fake_cfg)

    event = SimpleNamespace(
        user_id=10001, group_id=5, message_id=1, reply=None,
        get_user_id=lambda: "10001",
    )
    reply = await ai_plugin.chat(event, "有没有好用的翻墙工具")
    assert reply is not None
    assert "不参与" in reply or "没法讨论" in reply
    assert called == []          # 关键断言：模型没被调用


async def test_prompt_baseline_is_injected_into_messages(store, monkeypatch):
    """没命中规约时，兜底底线也要随系统提示词注入。"""
    from src.plugins import ai as ai_plugin

    monkeypatch.setattr(ai_plugin, "get_store", lambda: store)

    monkeypatch.setattr(llm, "get_store", lambda: store)
    monkeypatch.setattr(topics, "get_store", lambda: store)

    captured: dict = {}

    class FakeCompletions:
        @staticmethod
        async def create(**kw):
            captured["messages"] = kw.get("messages") or []
            raise RuntimeError("stop here")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions})

    async def fake_client(kind: str = "text"):
        return FakeClient()

    async def fake_cfg(raw: bool = False):
        return dict(llm.DEFAULTS)

    async def fake_quota(*a, **kw):
        return True, ""

    async def fake_scene(*a, **kw):
        return "场景"

    monkeypatch.setattr(llm, "get_client", fake_client)
    monkeypatch.setattr(llm, "ai_config", fake_cfg)
    monkeypatch.setattr(ai_plugin, "_check_quota", fake_quota)
    monkeypatch.setattr(ai_plugin, "_build_scene", fake_scene)

    event = SimpleNamespace(
        user_id=10001, group_id=5, message_id=1, reply=None,
        get_user_id=lambda: "10001",
    )
    assert await ai_plugin.chat(event, "帮我看看这个 Rust 报错") is None  # 模型报错
    blob = "\n".join(str(m.get("content", "")) for m in captured["messages"])
    assert "话题规约（底线" in blob
    assert "翻墙" in blob and "涉政" in blob
