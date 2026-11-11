import time
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import awareness as aw
from src.plugins import feedback as fb


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    aw.clear_group()
    aw._backfilled.clear()
    fb.reset()

    async def cfg():
        return dict(aw.DEFAULTS)

    monkeypatch.setattr(aw, "config", cfg)
    yield
    aw.clear_group()
    fb.reset()


class FakeBot:
    self_id = "1"

    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if self.exc:
            raise self.exc
        return self.result


def _hist(now):
    return {"messages": [
        {"time": now - 50, "message_id": 2, "user_id": 11, "sender": {"nickname": "小明", "card": ""},
         "message": [{"type": "text", "data": {"text": "第二条"}}]},
        {"time": now - 100, "message_id": 1, "user_id": 12, "sender": {"nickname": "小红", "card": "红红"},
         "message": [{"type": "text", "data": {"text": "第一条"}}, {"type": "image", "data": {"file": "a.jpg"}}]},
        {"time": now - 20, "message_id": 3, "user_id": 1, "sender": {"nickname": "bot"},
         "message": "我回复了"},
        {"time": now - 99999, "message_id": 0, "user_id": 11, "message": "太久以前"},
    ]}


async def test_backfill_orders_dedups_marks_bot_and_runs_once():
    now = time.time()
    await aw.record(5, 11, "小明", 2, Message([MessageSegment.text("第二条")]), now=now - 50)  # 已在缓冲里
    bot = FakeBot(_hist(now))
    assert await aw.backfill(bot, 5, now) == 2  # 第二条已有、太久以前的被丢弃
    out = aw.render_transcript(5, now=now).split("\n")
    assert [("第一条" in out[0]), ("第二条" in out[1]), ("星潮(我): 我回复了" in out[2])] == [True, True, True]
    assert "红红: 第一条 [图片]" in out[0]
    assert await aw.backfill(bot, 5, now) == 0 and len(bot.calls) == 1  # 每群只回填一次


async def test_backfill_failure_is_silent():
    assert await aw.backfill(FakeBot(exc=RuntimeError("unsupported")), 6) == 0
    assert aw.render_transcript(6) == ""


async def test_backfill_respects_buffer_size():
    now = time.time()
    rows = [{"time": now - 10 + i * 0.01, "message_id": i, "user_id": 11, "sender": {"nickname": "甲"},
             "message": [{"type": "text", "data": {"text": f"m{i}"}}]} for i in range(100)]
    await aw.backfill(FakeBot({"messages": rows}), 7, now)
    assert len(aw._buffers[7]) == aw.DEFAULTS["awareness_buffer_size"]


def test_feedback_polarity_and_dampen():
    now = time.time()
    fb.track_bot_message(900, 5, "我刚才说的话", now)
    assert fb.record_reaction(12345, [76], now=now) is None  # 不是机器人的消息
    assert fb.record_reaction(900, [76, 66], now=now) == "positive"
    assert not fb.dampened(5, now)
    assert fb.record_reaction(900, [268], now=now) == "negative"
    assert not fb.dampened(5, now)  # 1 次负面还不收敛
    assert fb.record_reaction(900, [77], now=now + 10) == "negative"
    assert fb.dampened(5, now + 20) and not fb.dampened(5, now + fb.DAMPEN_SECONDS + 100)
    assert not fb.dampened(6, now + 20)  # 别的群不受影响
    line = fb.prompt_line(5, now + 30)
    assert "正面 2 次" in line and "负面 2 次" in line and "收敛" in line
    assert fb.prompt_line(5, now + 99999) == ""  # 过期反馈不再提示


def test_feedback_track_capacity_and_bad_ids():
    for i in range(fb.TRACK_MAX + 20):
        fb.track_bot_message(i, 5, "x")
    assert len(fb._bot_msgs) == fb.TRACK_MAX
    fb.track_bot_message(None, 5, "x")
    fb.track_bot_message("abc", 5, "x")


async def test_emoji_like_notice_handler_parses_and_ignores_removal():
    fb.track_bot_message(900, 5, "hi")
    ev = SimpleNamespace(notice_type="group_msg_emoji_like", message_id=900, is_add=True,
                         likes=[{"emoji_id": "76", "count": 1}, {"emoji_id": "77", "count": 1}])
    assert await fb._is_emoji_like(ev) and not await fb._is_emoji_like(SimpleNamespace(notice_type="poke"))
    await fb.handle_emoji_like(ev)
    assert [p for _, p, _ in fb._events[5]] == ["positive", "negative"]
    await fb.handle_emoji_like(SimpleNamespace(notice_type="group_msg_emoji_like", message_id=900, is_add=False, likes=[{"emoji_id": "77"}]))
    assert len(fb._events[5]) == 2  # 取消表情不计
    await fb.handle_emoji_like(SimpleNamespace(notice_type="group_msg_emoji_like"))  # 缺字段不抛异常
