"""存表情：入库流程（审核/去重/上限）、按聊天内容挑表情、冷却、自主收集、指令触发。"""

import base64
import json

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from src import llm
from src.plugins import stickers as st
from src.plugins import stickersave as ss

GIF = b"GIF89a" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


def _desc(**over):
    d = {"safe": True, "text": "我裂开了", "meaning": "崩溃", "usage": "自己翻车时", "tags": "熊猫头,崩溃"}
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


@pytest.fixture
async def lib(store, monkeypatch, tmp_path):
    """隔离的表情包库：临时目录 + 独立数据库 + 假视觉模型（按调用顺序返回 replies 里的结果）。"""
    monkeypatch.setattr(st, "get_store", lambda: store)
    monkeypatch.setattr(ss, "get_store", lambda: store)
    monkeypatch.setattr(st, "sticker_dir", lambda: tmp_path / "stickers")
    st._invalidate()
    st._recent.clear()
    st._urls.clear()
    ss._sightings.clear()
    ss._done.clear()
    replies: list[str] = []
    calls: list[str] = []

    async def enabled():
        return True

    async def vision(ref, prompt, **kw):
        calls.append(ref)
        return replies.pop(0) if replies else _desc()

    monkeypatch.setattr(llm, "is_ai_enabled", enabled)
    monkeypatch.setattr(llm, "generate_vision", vision)
    import src.plugins.visionguard as vg

    monkeypatch.setattr(vg, "_decode_qr", lambda data: None)
    yield {"store": store, "replies": replies, "calls": calls, "dir": tmp_path / "stickers"}
    st._invalidate()


# ---------------------------------------------------------------- 解析

def test_sniff_image():
    assert st.sniff_image(GIF) == ("image/gif", "gif")
    assert st.sniff_image(PNG) == ("image/png", "png")
    assert st.sniff_image(b"\xff\xd8\xff\xe0rest") == ("image/jpeg", "jpg")
    assert st.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", "webp")
    assert st.sniff_image(b"<html>") is None


def test_parse_save_result():
    r = st.parse_save_result("好的：" + _desc() + " 以上")
    assert r == {"safe": True, "text": "我裂开了", "meaning": "崩溃", "usage": "自己翻车时", "tags": "熊猫头,崩溃"}
    assert st.parse_save_result(_desc(safe=False))["safe"] is False
    assert st.parse_save_result(_desc(safe="false"))["safe"] is False
    assert st.parse_save_result("看不懂") is None
    assert st.parse_save_result(None) is None


@pytest.mark.parametrize("text,ok", [
    ("存表情", True), ("/存表情", True), ("星潮 存一下！", True), ("偷了~", True), ("收藏", True),
    ("存表情包。", True), ("存", False), ("这个表情不错", False), ("帮我存表情吧", False),
])
def test_triggers(text, ok):
    assert (ss.normalize_trigger(text) in ss.TRIGGERS) is ok


# ---------------------------------------------------------------- 入库

async def test_save_ok_writes_file_and_row(lib):
    code, row = await st.save_image(GIF, group_id=555, saved_by=10001, source="cmd")
    assert code == "ok"
    assert row["meaning"] == "崩溃" and row["usage"] == "自己翻车时" and row["group_id"] == 555
    assert (lib["dir"] / row["file"]).read_bytes() == GIF
    assert lib["calls"][0].startswith("data:image/gif;base64,")
    assert st.describe(row) == f"#{row['id']}「我裂开了」崩溃·自己翻车时"


async def test_save_duplicate_does_not_call_model_again(lib):
    _, first = await st.save_image(GIF, group_id=555, saved_by=1, source="cmd")
    code, row = await st.save_image(GIF, group_id=666, saved_by=2, source="cmd")
    assert code == "duplicate" and row["id"] == first["id"]
    assert len(lib["calls"]) == 1


async def test_save_rejections(lib, monkeypatch):
    lib["replies"].append(_desc(safe=False))
    assert (await st.save_image(GIF, group_id=1, saved_by=1, source="cmd"))[0] == "unsafe"
    lib["replies"].append("模型胡说")
    assert (await st.save_image(PNG, group_id=1, saved_by=1, source="cmd"))[0] == "failed"
    assert (await st.save_image(b"not an image", group_id=1, saved_by=1, source="cmd"))[0] == "not_image"
    assert (await st.save_image(GIF + b"\x00" * st.MAX_BYTES, group_id=1, saved_by=1, source="cmd"))[0] == "too_big"
    import src.plugins.visionguard as vg

    monkeypatch.setattr(vg, "_decode_qr", lambda data: "https://weixin.qq.com/g/xxx")
    assert (await st.save_image(GIF + b"q", group_id=1, saved_by=1, source="cmd"))[0] == "qrcode"
    monkeypatch.setattr(vg, "_decode_qr", lambda data: None)

    async def off():
        return False

    monkeypatch.setattr(llm, "is_ai_enabled", off)
    assert (await st.save_image(GIF + b"n", group_id=1, saved_by=1, source="cmd"))[0] == "no_ai"
    assert not list(lib["dir"].glob("*")) if lib["dir"].exists() else True  # 被拒的一张都没落盘
    assert await lib["store"].list_stickers() == []


async def test_save_full_library(lib, monkeypatch):
    monkeypatch.setattr(st, "MAX_LIBRARY", 1)
    assert (await st.save_image(GIF, group_id=1, saved_by=1, source="cmd"))[0] == "ok"
    assert (await st.save_image(PNG, group_id=1, saved_by=1, source="cmd"))[0] == "full"


async def test_delete_removes_file(lib):
    _, row = await st.save_image(GIF, group_id=1, saved_by=1, source="cmd")
    assert (await st.delete(row["id"]))["id"] == row["id"]
    assert not (lib["dir"] / row["file"]).exists()
    assert await st.delete(row["id"]) is None


# ---------------------------------------------------------------- 挑选与发送

async def _seed(lib):
    lib["replies"] += [
        _desc(text="我裂开了", meaning="崩溃", usage="自己翻车时", tags="熊猫头,崩溃"),
        _desc(text="", meaning="开心", usage="好消息庆祝", tags="开心,庆祝"),
        _desc(text="吃瓜", meaning="围观看热闹", usage="群里有八卦时", tags="吃瓜,围观"),
    ]
    ids = []
    for i, gid in enumerate((555, 555, 666)):
        _, row = await st.save_image(GIF + bytes([i]), group_id=gid, saved_by=1, source="cmd")
        ids.append(f"L{row['id']}")
    return ids


async def test_catalog_puts_relevant_first(lib):
    crack, happy, melon = await _seed(lib)
    menu, ids = await st.catalog(555, "代码又崩溃了，线上翻车")
    assert ids[0] == crack and "「我裂开了」崩溃·自己翻车时" in menu
    menu, ids = await st.catalog(555, "隔壁群在吃瓜看八卦")
    assert ids[0] == melon


async def test_same_group_preferred_when_nothing_relevant(lib):
    crack, happy, melon = await _seed(lib)
    for _ in range(10):
        _, ids = await st.catalog(555, "完全不相关的话")
        assert set(ids[:2]) == {crack, happy}  # 本群存的两张排在前面


async def test_cooldown_excludes_recently_sent(lib):
    crack, happy, melon = await _seed(lib)
    await st.note_used(555, crack)
    _, ids = await st.catalog(555, "崩溃翻车")
    assert crack not in ids
    _, ids = await st.catalog(666, "崩溃翻车")  # 只在发过的那个群冷却
    assert ids[0] == crack
    row = await lib["store"].get_sticker(int(crack[1:]))
    assert row["uses"] == 1 and row["last_used"]


async def test_pick_by_mood_and_message(lib):
    crack, happy, melon = await _seed(lib)
    assert await st.pick_by_mood("吃瓜", 555) == melon
    msg = await st.sticker_message(crack)
    seg = msg[0]
    assert seg.type == "image" and seg.data["sub_type"] == 1
    assert base64.b64decode(seg.data["file"].removeprefix("base64://")) == GIF + bytes([0])
    assert await st.sticker_message("L99999") is None


async def test_ensure_library_counts_local(lib):
    await _seed(lib)

    class Bot:
        async def call_api(self, name, **kw):
            raise RuntimeError("不支持")

    st._last_refresh = 0.0
    assert await st.ensure_library(Bot()) == 3


# ---------------------------------------------------------------- 自主收集

def _seg(file="abc.gif"):
    return MessageSegment("image", {"file": file, "url": f"https://q/{file}?sig=1", "sub_type": 1})


def test_sighting_needs_two_different_people():
    ss._sightings.clear()
    ss._done.clear()
    assert not ss.note_sighting("k", 1, 555, _seg(), now=0)
    assert not ss.note_sighting("k", 1, 555, _seg(), now=10)  # 同一个人再发不算
    assert ss.note_sighting("k", 2, 666, _seg(), now=20)      # 跨群的第二个人：触发
    assert not ss.note_sighting("k", 3, 555, _seg(), now=30)  # 触发过就不再重复


def test_sighting_window_expires():
    ss._sightings.clear()
    ss._done.clear()
    assert not ss.note_sighting("k", 1, 555, _seg(), now=0)
    assert not ss.note_sighting("k", 2, 555, _seg(), now=ss.AUTO_WINDOW + 1)  # 太久了，重新计
    assert ss.note_sighting("k", 3, 555, _seg(), now=ss.AUTO_WINDOW + 2)


async def test_auto_save_respects_daily_cap(lib, monkeypatch):
    async def fake_bytes(seg):
        return GIF + seg.data["file"].encode()

    monkeypatch.setattr(ss, "_image_bytes", fake_bytes)
    monkeypatch.setattr(ss, "AUTO_DAILY", 1)
    await ss._auto_save(_seg("a"), 555)
    await ss._auto_save(_seg("b"), 555)
    rows = await lib["store"].list_stickers()
    assert len(rows) == 1 and rows[0]["source"] == "auto" and rows[0]["saved_by"] == 0


# ---------------------------------------------------------------- 指令

def _event(text="存表情", reply_image=True):
    data = {
        "time": 0, "self_id": 1, "post_type": "message", "sub_type": "normal",
        "user_id": 10001, "message_type": "group", "message_id": 500,
        "message": [{"type": "text", "data": {"text": text}}],
        "original_message": [], "raw_message": "", "font": 0,
        "sender": {"user_id": 10001, "nickname": "测试"}, "group_id": 555, "to_me": False,
    }
    if reply_image is not None:
        data["reply"] = {
            "time": 0, "message_type": "group", "message_id": 499, "real_id": 499,
            "message": [{"type": "image", "data": {"file": "x.gif", "url": "https://q/x.gif", "sub_type": 1}}]
            if reply_image else [{"type": "text", "data": {"text": "hi"}}],
            "sender": {"user_id": 777, "nickname": "路人", "card": ""},
        }
    return GroupMessageEvent.model_validate(data)


class _Matcher:
    def __init__(self):
        self.sent = []

    async def finish(self, msg):
        self.sent.append(str(msg))
        raise _Finished


class _Finished(Exception):
    pass


class _Bot:
    self_id = "1"

    async def call_api(self, name, **kw):
        return {"message": []}


async def _run_save(event):
    m = _Matcher()
    try:
        await ss.handle_save(_Bot(), event, m)
    except _Finished:
        pass
    return m.sent[-1]


async def test_command_saves_replied_sticker(lib, monkeypatch):
    async def fake_bytes(seg):
        assert seg.data["file"] == "x.gif"
        return GIF

    monkeypatch.setattr(ss, "_image_bytes", fake_bytes)
    assert await ss._is_save_request(_event())
    assert not await ss._is_save_request(_event(text="存表情", reply_image=None))  # 没回复也没图
    out = await _run_save(_event())
    assert "存好啦" in out and "「我裂开了」" in out
    assert "早就存过" in await _run_save(_event())


async def test_command_without_image_and_daily_limit(lib, monkeypatch):
    assert "要存哪张" in await _run_save(_event(reply_image=False))

    async def fake_bytes(seg):
        return GIF + b"x"

    monkeypatch.setattr(ss, "_image_bytes", fake_bytes)
    monkeypatch.setattr(ss, "DAILY_PER_USER", 0)
    assert "明天再来" in await _run_save(_event())


async def test_module_switch(lib):
    await lib["store"].set_kv("sticker_collect_enabled", "false")
    assert not await ss._is_save_request(_event())
    assert not await ss.auto_enabled()
    await lib["store"].set_kv("sticker_collect_enabled", "true")
    await lib["store"].set_kv("sticker_auto_collect", "false")
    assert await ss._is_save_request(_event()) and not await ss.auto_enabled()
