"""视觉请求的提供商路由、附图识别失败提示、附图识别与群聊感知开关解耦。"""

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import ai, awareness as aw


@pytest.fixture
async def kv(store, monkeypatch):
    monkeypatch.setattr(ai, "get_store", lambda: store)
    return store


async def test_vision_via_deepseek_needs_provider_and_model(kv):
    await kv.set_kv("ai_vision_model", "deepseek-flash")
    assert not await ai._vision_via_deepseek()  # 提供商仍是默认
    await kv.set_kv("ai_provider", "deepseek")
    assert not await ai._vision_via_deepseek()  # 选了 deepseek 但没填密钥 → 回落默认
    await kv.set_kv("ai_deepseek_api_key", "sk-x")
    assert await ai._vision_via_deepseek()
    await kv.set_kv("ai_vision_model", "glm-4v-flash")
    assert not await ai._vision_via_deepseek()  # 非 deepseek 模型仍走默认提供商


async def test_get_client_vision_uses_deepseek_credentials(kv, monkeypatch):
    import openai

    created = []

    class FakeClient:
        def __init__(self, api_key, base_url):
            created.append((api_key, base_url))

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    ai._clients.clear()
    await kv.set_kv("ai_provider", "deepseek")
    await kv.set_kv("ai_deepseek_api_key", "sk-ds")
    await kv.set_kv("ai_vision_model", "deepseek-flash")
    assert await ai.get_client("vision") is not None
    assert created == [("sk-ds", ai.DEEPSEEK_BASE_URL)]
    ai._clients.clear()


async def test_direct_images_ignore_awareness_switch(monkeypatch):
    async def fake_describe(seg, prompt):
        return "一只猫"

    async def ok():
        return True

    async def off():
        return {"awareness_enabled": False}

    monkeypatch.setattr(aw, "_vision_describe", fake_describe)
    monkeypatch.setattr(aw, "_vision_available", ok)
    monkeypatch.setattr(aw, "config", off)
    msg = Message([MessageSegment.text("看"), MessageSegment.image("b.jpg")])
    assert await aw.describe_message_images(msg) == ["一只猫"]


async def test_attached_note_tells_model_when_recognition_fails(monkeypatch):
    async def none(message, limit=3):
        return []

    monkeypatch.setattr(aw, "describe_message_images", none)

    class Ev:
        message = Message([MessageSegment.text("这是啥"), MessageSegment.image("c.jpg")])
        reply = None

    note = await ai._attached_images_note(Ev())
    assert "识别失败" in note and "不要猜测" in note

    class Ev2:
        message = Message([MessageSegment.text("纯文字")])
        reply = None

    assert await ai._attached_images_note(Ev2()) == ""
