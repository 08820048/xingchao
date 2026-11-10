import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.markdown import md_to_qq
from src.plugins import expressions as ex
from src.plugins import proactive as pa
from src.plugins import stickers as st


def test_render_faces_converts_known_and_drops_unknown_and_extra():
    msg = ex.render_faces("好的[表情:笑哭]没问题[表情:不存在][表情:赞][表情:OK]")
    types = [s.type for s in msg]
    assert types.count("face") == 2  # 超过 2 个的占位符被去掉
    assert msg[1].type == "face" and msg[1].data["id"] == str(ex.FACES["笑哭"])
    assert msg.extract_plain_text() == "好的没问题"
    plain = ex.render_faces("没有表情")
    assert plain.extract_plain_text() == "没有表情" and all(s.type == "text" for s in plain)
    assert [s.type for s in ex.render_faces("[表情：赞]")] == ["face"]  # 全角冒号


def test_strip_faces_for_voice():
    assert ex.strip_faces("好呀[表情:笑哭]走起") == "好呀走起"


def test_markdown_downgrade_keeps_face_tokens():
    assert "[表情:笑哭]" in md_to_qq("**好的**[表情:笑哭]")


def test_persona_block_mentions_face_usage():
    from src.plugins.persona import DEFAULT_PERSONA, render_persona

    assert "[表情:名称]" in render_persona(DEFAULT_PERSONA)


def test_emoji_reaction_ids_are_system_face_ids():
    assert pa.EMOJIS["笑哭"] == str(ex.FACES["笑哭"]) and pa.EMOJIS["赞"] == str(ex.FACES["赞"])


def test_parse_decision_sticker():
    ids = [10]
    assert pa.parse_decision('{"action":"sticker","sticker":2}', ids, stickers=["a", "b"]) == {
        "action": "sticker", "sticker_id": "b"}
    assert pa.parse_decision('{"action":"sticker","sticker":9}', ids, stickers=["a"])["action"] == "skip"
    assert pa.parse_decision('{"action":"sticker","sticker":1}', ids)["action"] == "skip"  # 没有表情包库


def test_parse_face_list_formats():
    assert st.parse_face_list(["https://a/1.png", "bad", "https://a/1.png"]) == ["https://a/1.png"]
    assert st.parse_face_list({"data": [{"url": "https://b/2.png"}, {"nourl": 1}]}) == ["https://b/2.png"]
    assert st.parse_face_list(None) == []
    assert st.sticker_id("https://a/x.png?sig=1") == st.sticker_id("https://a/x.png?sig=2")


class FakeBot:
    def __init__(self, urls):
        self.urls, self.calls = urls, []

    async def call_api(self, name, **kw):
        self.calls.append(name)
        return self.urls


async def test_library_tags_once_and_picks_by_mood(store, monkeypatch):
    monkeypatch.setattr(st, "get_store", lambda: store)
    st._urls.clear()
    st._last_refresh = 0.0
    st._tag_day = ("", 0)
    tagged = []

    async def fake_tag(url):
        tagged.append(url)
        return {"https://a/1.png": "无语", "https://a/2.png": "开心"}[url]

    monkeypatch.setattr(st, "_tag_one", fake_tag)
    bot = FakeBot(["https://a/1.png", "https://a/2.png"])
    assert await st.ensure_library(bot) == 2
    assert await st.ensure_library(bot) == 2 and bot.calls == ["fetch_custom_face"]  # 6 小时内不重复拉取
    st._last_refresh = 0.0
    assert await st.ensure_library(bot) == 2 and len(tagged) == 2  # 已有标签，不重复打
    menu, ids = await st.catalog()
    assert "无语" in menu and "开心" in menu and len(ids) == 2
    sid = await st.pick_by_mood("有点无语")
    assert sid == st.sticker_id("https://a/1.png")
    msg = st.sticker_message(sid)
    assert msg[0].type == "image" and msg[0].data["sub_type"] == 1 and msg[0].data["file"] == "https://a/1.png"
    assert await st.pick_by_mood("完全没匹配的心情") in (st.sticker_id("https://a/1.png"), st.sticker_id("https://a/2.png"))


async def test_library_empty_or_api_failure(store, monkeypatch):
    monkeypatch.setattr(st, "get_store", lambda: store)
    st._urls.clear()

    class Boom:
        async def call_api(self, *a, **k):
            raise RuntimeError("unsupported")

    assert await st.ensure_library(Boom(), force=True) == 0
    assert await st.pick_by_mood("开心") is None
