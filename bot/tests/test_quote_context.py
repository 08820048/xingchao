"""引用回复：被引用的原文必须进 user 轮，模型才能正确理解用户那句话。

历史问题：引用内容只放在场景 system 提示里，用户自己说的话又极短（一个「0000」），
模型把「0000」当成独立话题自由联想，答非所问。
"""


from types import SimpleNamespace

import pytest

from src import llm
from src.plugins import ai as ai_plugin


def _event(**over):
    data = {
        "time": 0, "self_id": 1, "post_type": "message", "sub_type": "normal",
        "user_id": 10001, "message_type": "group", "message_id": 500,
        "message": [{"type": "at", "data": {"qq": "1"}},
                    {"type": "text", "data": {"text": " 0000"}}],
        "original_message": [], "raw_message": "", "font": 0,
        "sender": {"user_id": 10001, "nickname": "测试"},
        "group_id": 555, "to_me": True,
    }
    if "reply_text" in over:
        data["reply"] = {
            "time": 0, "message_type": "group", "message_id": 499, "real_id": 499,
            "message": [{"type": "text", "data": {"text": over["reply_text"]}}],
            "sender": {"user_id": over.get("reply_uid", 777),
                       "nickname": over.get("reply_name", "路人"), "card": ""},
        }
    return ai_plugin.GroupMessageEvent.model_validate(data)


class _Bot:
    self_id = "1"


# ---------------------------------------------------------------- quote_block

async def test_quote_block_marks_bot_own_message():
    ev = _event(reply_text=" 入群验证未通过。现在回复 9-9 的结果还来得及。",
                reply_uid=1, reply_name="星潮酱")
    block = await ai_plugin.quote_block(ev, _Bot())
    assert "星潮酱（你自己）" in block
    assert "9-9" in block
    assert "下面这行才是用户自己说的话" in block


async def test_quote_block_marks_other_people():
    ev = _event(reply_text="把 port 改成 8080", reply_uid=777, reply_name="老王")
    block = await ai_plugin.quote_block(ev, _Bot())
    assert "老王" in block and "你自己" not in block
    assert "8080" in block


async def test_quote_block_empty_when_no_reply():
    assert await ai_plugin.quote_block(_event(), _Bot()) == ""


async def test_quote_block_handles_media_only_quote():
    ev = _event(reply_text="", reply_uid=777, reply_name="老王")
    block = await ai_plugin.quote_block(ev, _Bot())
    assert "没有文字" in block


async def test_quote_block_truncates_long_quote():
    ev = _event(reply_text="很长的一段话" * 200, reply_uid=777)
    block = await ai_plugin.quote_block(ev, _Bot())
    assert len(block) < ai_plugin.QUOTE_MAX_CHARS + 200
    assert "…" in block


# ---------------------------------------------------------------- 注入 user 轮

@pytest.fixture
async def captured(monkeypatch, store):
    """跑一遍 chat()，抓取发给模型的消息数组。"""
    # 各插件都是 `from src.store import get_store`，绑定了模块级名字，
    # 必须逐个 patch 模块自身，否则会连到真实库（还会因为连接没关让 pytest 退不出去）
    for mod_name in ("ai", "awareness", "persona", "memory", "topics", "blog"):
        mod = getattr(__import__("src.plugins", fromlist=[mod_name]), mod_name, None)
        if mod is not None and hasattr(mod, "get_store"):
            monkeypatch.setattr(mod, "get_store", lambda: store, raising=False)
    box: dict = {}

    class FakeCompletions:
        @staticmethod
        async def create(**kw):
            box["messages"] = kw.get("messages") or []
            # 返回一个"成功但无工具调用"的回复，让 chat() 走完历史写入
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="好的", tool_calls=None))])

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
    return box


async def _run_chat(event, captured):
    """模拟 _handle_ai 里对 text 的组装，再交给 chat()。"""
    quote = await ai_plugin.quote_block(event, _Bot())
    raw_text = event.message.extract_plain_text().strip()
    text = quote + raw_text
    await ai_plugin.chat(event, text, bot=_Bot())
    return captured["messages"]


async def test_chat_user_turn_contains_quote(captured):
    ev = _event(reply_text="入群验证未通过。现在回复 9-9 的结果还来得及。",
                reply_uid=1, reply_name="星潮酱")
    messages = await _run_chat(ev, captured)
    user_turns = [m for m in messages if m["role"] == "user"]
    assert user_turns, "应有 user 轮"
    content = str(user_turns[-1]["content"])
    # 关键：引用原文在 user 轮里，和用户的话在一起
    assert "9-9" in content
    assert "0000" in content
    assert content.index("9-9") < content.index("0000")


async def test_quote_persists_into_history(captured):
    """引用内容要写进上下文，否则下一轮追问时又丢了。"""
    ev = _event(reply_text="把 port 改成 8080", reply_uid=777, reply_name="老王")
    await _run_chat(ev, captured)
    history = ai_plugin._get_history(555)
    assert any("8080" in str(m.get("content", "")) for m in history)


def test_quote_sender_falls_back_to_qq():
    ev = _event(reply_text="x", reply_uid=123456, reply_name="")
    uid, who = ai_plugin._quote_sender(ev, _Bot())
    assert uid == 123456 and who == "123456"


def test_quote_sender_without_bot_context():
    """没有 bot 对象时（离线渲染）不该崩，也不该误标「你自己」。"""
    ev = _event(reply_text="x", reply_uid=1, reply_name="星潮酱")
    uid, who = ai_plugin._quote_sender(ev, None)
    assert uid == 1 and who == "星潮酱"


# ---------------------------------------------------------------- 文本组装各分支

def test_compose_quote_plus_text():
    text, hist = ai_plugin.compose_user_text(
        raw_text="0000", quote="【引用】9-9\n", img_note="", pure_at=False,
    )
    assert text.startswith("【引用】9-9")
    assert text.endswith("0000")
    assert hist is None            # 没有图时历史直接用 text（已含引用）


def test_compose_quote_without_text_asks_to_use_quote():
    text, _ = ai_plugin.compose_user_text(
        raw_text="", quote="【引用】某条消息\n", img_note="", pure_at=True,
    )
    assert "某条消息" in text and "针对引用内容回应" in text


def test_compose_pure_at_without_quote_unchanged():
    text, hist = ai_plugin.compose_user_text(
        raw_text="", quote="", img_note="", pure_at=True,
    )
    assert text == ai_plugin.PURE_AT_PROMPT
    assert hist == "@星潮"


def test_compose_with_image_note_keeps_quote_in_history():
    text, hist = ai_plugin.compose_user_text(
        raw_text="看这个", quote="【引用】配置说明\n", img_note="\n（用户附带的图片内容：报错截图）",
        pure_at=False,
    )
    assert "看这个" in text and "报错截图" in text
    assert hist is not None and "配置说明" in hist
