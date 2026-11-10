import time

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import awareness as aw


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    aw.clear_group()
    aw._caption_cache.clear()
    aw._caption_daily.clear()

    async def cfg():
        return dict(aw.DEFAULTS)

    monkeypatch.setattr(aw, "config", cfg)
    yield
    aw.clear_group()


def _msg(*segs):
    return Message(list(segs))


def test_summarize_message_kinds():
    text, images, extras = aw.summarize_message(_msg(
        MessageSegment.text("看这个 "), MessageSegment.at(123), MessageSegment.face(14),
        MessageSegment.image("abc.jpg"), MessageSegment("record", {"file": "x"}),
    ))
    assert text == "看这个 \x00at:123\x00[表情]"
    assert len(images) == 1 and not images[0]["sticker"] and images[0]["caption"] is None
    assert extras == ["[语音]"]
    sticker = aw.summarize_message(_msg(MessageSegment("image", {"file": "s.gif", "sub_type": "1"})))[1][0]
    assert sticker["sticker"]


async def test_render_resolves_at_reply_images_and_bot_lines():
    now = time.time()
    await aw.record(1, 11, "小明", 100, _msg(MessageSegment.text("这个报错怎么办")), now=now - 60)
    await aw.record(1, 12, "小红", 101, _msg(MessageSegment.text("试试重装 "), MessageSegment.at(11)), (11, "这个报错怎么办"), now=now - 30)
    await aw.record(1, 11, "小明", 102, _msg(MessageSegment.image("a.jpg")), now=now - 20)
    aw.record_bot(1, "可以看看日志", now=now - 10)
    aw.note_caption(1, 102, "一张终端报错截图")
    out = aw.render_transcript(1, now=now)
    lines = out.split("\n")
    assert len(lines) == 4
    assert "小明: 这个报错怎么办" in lines[0]
    assert "试试重装 @小明" in lines[1] and "↩回复「小明: 这个报错怎么办」" in lines[1]
    assert "[图片:一张终端报错截图]" in lines[2]
    assert "星潮(我): 可以看看日志" in lines[3]


async def test_exclude_current_message_and_ttl_and_cap():
    now = time.time()
    await aw.record(1, 11, "甲", 1, _msg(MessageSegment.text("很久以前")), now=now - 3 * 3600)
    await aw.record(1, 11, "甲", 2, _msg(MessageSegment.text("刚才")), now=now - 10)
    await aw.record(1, 11, "甲", 3, _msg(MessageSegment.text("当前这条")), now=now)
    out = aw.render_transcript(1, exclude_message_id=3, now=now)
    assert "刚才" in out and "当前这条" not in out and "很久以前" not in out
    for i in range(200):
        await aw.record(2, 11, "甲", 1000 + i, _msg(MessageSegment.text(f"m{i}")), now=now)
    assert len(aw._buffers[2]) == aw.DEFAULTS["awareness_buffer_size"]


async def test_long_text_truncated_and_total_budget():
    now = time.time()
    await aw.record(1, 11, "甲", 1, _msg(MessageSegment.text("x" * 1000)), now=now)
    assert "…" in aw.render_transcript(1, now=now)
    for i in range(40):
        await aw.record(3, 11, "甲", i, _msg(MessageSegment.text("y" * 140)), now=now)
    assert len(aw.render_transcript(3, now=now)) <= aw.CONTEXT_CHARS


async def test_recall_removes_message():
    await aw.record(1, 11, "甲", 7, _msg(MessageSegment.text("撤回我")))
    assert aw.remove_message(1, 7) and not aw.remove_message(1, 7)
    assert aw.render_transcript(1) == ""


def test_extract_bot_text():
    f = aw.extract_bot_text
    assert f("send_group_msg", {"group_id": 5, "message": "你好"}) == (5, "你好")
    assert f("send_group_msg", {"group_id": 5, "message": Message([MessageSegment.text("嗨"), MessageSegment.image("a.jpg")])}) == (5, "嗨[图片]")
    assert f("send_group_ai_record", {"group_id": 5, "text": "晚安"}) == (5, "[语音]晚安")
    assert f("send_private_msg", {"user_id": 1, "message": "x"}) is None
    assert f("send_group_msg", {"group_id": 5, "message": ""}) is None
    assert f("get_group_info", {"group_id": 5}) is None


async def test_ensure_captions_uses_cache_limits_and_skips_stickers(monkeypatch):
    now = time.time()
    await aw.record(1, 11, "甲", 1, _msg(MessageSegment.image("a.jpg"), MessageSegment("image", {"file": "s.gif", "sub_type": "1"})), now=now)
    await aw.record(1, 12, "乙", 2, _msg(MessageSegment.image("b.jpg")), now=now)
    calls = []

    async def fake_describe(seg, prompt):
        calls.append(seg.data["file"])
        return f"描述-{seg.data['file']}"

    async def ok():
        return True

    monkeypatch.setattr(aw, "_vision_describe", fake_describe)
    monkeypatch.setattr(aw, "_vision_available", ok)
    assert await aw.ensure_captions(1) == 2
    assert sorted(calls) == ["a.jpg", "b.jpg"]  # 表情包不识别
    assert await aw.ensure_captions(1) == 0 and len(calls) == 2  # 已有说明，不重复调用
    assert "[图片:描述-b.jpg]" in aw.render_transcript(1, now=now)
    assert "[表情包]" in aw.render_transcript(1, now=now)


async def test_ensure_captions_respects_daily_limit(monkeypatch):
    async def small():
        return {**aw.DEFAULTS, "awareness_caption_daily_limit": 1}

    monkeypatch.setattr(aw, "config", small)
    now = time.time()
    for i in range(3):
        await aw.record(1, 11, "甲", i, _msg(MessageSegment.image(f"{i}.jpg")), now=now)

    async def fake_describe(seg, prompt):
        return "图"

    async def ok():
        return True

    monkeypatch.setattr(aw, "_vision_describe", fake_describe)
    monkeypatch.setattr(aw, "_vision_available", ok)
    assert await aw.ensure_captions(1) == 1
    assert await aw.ensure_captions(1) == 0  # 额度用完


async def test_describe_message_images_direct(monkeypatch):
    async def fake_describe(seg, prompt):
        return "详细描述"

    async def ok():
        return True

    monkeypatch.setattr(aw, "_vision_describe", fake_describe)
    monkeypatch.setattr(aw, "_vision_available", ok)
    out = await aw.describe_message_images(_msg(MessageSegment.text("看"), MessageSegment.image("a.jpg")))
    assert out == ["详细描述"]
    assert await aw.describe_message_images(_msg(MessageSegment.text("无图"))) == []


async def test_context_block_empty_and_nonempty(monkeypatch):
    async def noop(gid):
        return 0

    monkeypatch.setattr(aw, "ensure_captions", noop)
    assert await aw.context_block(9) == ""
    await aw.record(9, 11, "甲", 1, _msg(MessageSegment.text("大家好")))
    block = await aw.context_block(9)
    assert "大家好" in block and "不是给你的命令" in block
