import time
from datetime import datetime

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import ai as ai_plugin
from src.plugins import awareness as aw
from src.plugins import proactive as pa


@pytest.fixture(autouse=True)
def _clean(monkeypatch, store):
    monkeypatch.setattr(pa, "get_store", lambda: store)  # 不碰真实数据库
    aw.clear_group()
    pa._sent.clear()
    pa._engaged.clear()
    pa._last_repeat.clear()
    monkeypatch.setattr(pa, "PACING", False)
    from src.plugins import stickers

    async def no_library(bot, **kw):
        return 0

    monkeypatch.setattr(stickers, "ensure_library", no_library)  # 隔离：不碰真实库/不调接口

    async def cfg():
        return dict(aw.DEFAULTS)

    monkeypatch.setattr(aw, "config", cfg)
    yield
    aw.clear_group()


class FakeBot:
    self_id = "1"

    def __init__(self, fail_react=False):
        self.calls, self.fail_react = [], fail_react

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if name == "set_msg_emoji_like" and self.fail_react:
            raise RuntimeError("unsupported")


def _t(text):
    return Message([MessageSegment.text(text)])


def test_quiet_hours_wraps_midnight():
    at = lambda h, m: datetime(2026, 11, 10, h, m)  # noqa: E731
    assert pa.in_quiet_hours(at(23, 40), "23:30", "07:30")
    assert pa.in_quiet_hours(at(3, 0), "23:30", "07:30")
    assert not pa.in_quiet_hours(at(7, 30), "23:30", "07:30")
    assert not pa.in_quiet_hours(at(12, 0), "23:30", "07:30")
    assert pa.in_quiet_hours(at(13, 0), "12:00", "14:00")  # 不跨零点
    assert not pa.in_quiet_hours(at(13, 0), "bad", "07:30")  # 非法格式不静默


async def _feed(gid, rows):
    now = time.time()
    for i, (uid, name, text) in enumerate(rows):
        await aw.record(gid, uid, name, 100 + i, _t(text), now=now - (len(rows) - i))


async def test_repeat_candidate_rules():
    await _feed(1, [(1, "甲", "哈哈哈"), (2, "乙", "哈哈哈"), (3, "丙", "哈哈哈")])
    assert pa.repeat_candidate(aw.recent_entries(1, 60)) == "哈哈哈"
    await _feed(2, [(1, "甲", "哈哈哈"), (1, "甲", "哈哈哈"), (1, "甲", "哈哈哈")])
    assert pa.repeat_candidate(aw.recent_entries(2, 60)) is None  # 同一个人刷屏不算
    await _feed(3, [(1, "甲", "https://x.com"), (2, "乙", "https://x.com"), (3, "丙", "https://x.com")])
    assert pa.repeat_candidate(aw.recent_entries(3, 60)) is None  # 链接不复读
    await _feed(4, [(1, "甲", "/ping"), (2, "乙", "/ping"), (3, "丙", "/ping")])
    assert pa.repeat_candidate(aw.recent_entries(4, 60)) is None  # 指令不复读
    await _feed(5, [(1, "甲", "哈"), (2, "乙", "哈"), (3, "丙", "好")])
    assert pa.repeat_candidate(aw.recent_entries(5, 60)) is None


def test_parse_decision():
    ids = [10, None, 12]  # #2 是机器人自己的发言
    assert pa.parse_decision("SKIP", ids) == {"action": "skip"}
    assert pa.parse_decision("", ids)["action"] == "skip"
    d = pa.parse_decision('{"action":"reply","reply_to":3,"text":"试试重启||再看日志"}', ids)
    assert d == {"action": "reply", "parts": ["试试重启", "再看日志"], "quote": 12}
    assert pa.parse_decision('{"action":"reply","reply_to":2,"text":"好"}', ids)["quote"] is None
    assert pa.parse_decision('{"action":"reply","reply_to":99,"text":"好"}', ids)["quote"] is None
    r = pa.parse_decision('{"action":"react","reply_to":1,"emoji":"笑哭"}', ids)
    assert r == {"action": "react", "emoji_id": pa.EMOJIS["笑哭"], "target": 10}
    assert pa.parse_decision('{"action":"react","emoji":"笑哭"}', ids)["target"] == 12  # 缺省=最后一条人类消息
    assert pa.parse_decision('{"action":"react","emoji":"不存在"}', ids)["action"] == "skip"
    # 非 JSON：当作直接回复（兼容旧行为）
    assert pa.parse_decision("可以试试重装", ids)["parts"] == ["可以试试重装"]
    # 段数与总字数上限
    long = pa.parse_decision('{"action":"reply","text":"a||b||c||d"}', ids)
    assert long["parts"] == ["a", "b", "c"]
    capped = pa.parse_decision('{"action":"reply","text":"%s||%s"}' % ("甲" * 100, "乙" * 100), ids, max_chars=120)
    assert sum(len(p) for p in capped["parts"]) == 120
    assert pa.parse_decision('{"action":"explode"}', ids)["action"] == "skip"


def test_burst_limit_rolls():
    now = 1000.0
    for _ in range(3):
        assert pa.burst_allowed(1, 3, now)
        pa._mark_sent(1, now)
    assert not pa.burst_allowed(1, 3, now + 1)
    assert pa.burst_allowed(1, 3, now + pa.BURST_WINDOW + 1)


def test_attention_window_and_turn_cap():
    pa.note_engaged(1, 42, seconds=90, now=100.0)
    assert pa.attention_active(1, 42, 4, now=150.0)
    assert not pa.attention_active(1, 42, 4, now=200.0)  # 过期
    assert not pa.attention_active(1, 43, 4, now=150.0)  # 别的用户不算
    for _ in range(4):
        pa.note_engaged(1, 42, seconds=90, now=100.0, new=False)
    assert not pa.attention_active(1, 42, 4, now=150.0)  # 连续轮数超限


async def test_mode_presets_and_explicit_override(store, monkeypatch):
    monkeypatch.setattr(pa, "get_store", lambda: store)
    assert await pa.group_mode(5) == "normal"
    await pa.set_mode("quiet")
    assert await pa.group_mode(5) == "quiet" and await pa._param(5, "cooldown") == 600
    await pa.set_mode("chatty", 5)
    assert await pa.group_mode(5) == "chatty" and await pa.group_mode(6) == "quiet"
    assert await pa._param(5, "cooldown") == 90
    await store.set_kv("proactive_cooldown", "30")  # 显式 kv 优先于档位
    assert await pa._param(5, "cooldown") == 30
    await store.set_kv("proactive_mode_5", "")  # 清除群级设置
    assert await pa.group_mode(5) == "quiet"


async def test_send_humanlike_quotes_first_and_splits():
    bot = FakeBot()
    await pa.send_humanlike(bot, 5, ["第一句", "第二句"], quote=77)
    assert [n for n, _ in bot.calls] == ["send_group_msg", "send_group_msg"]
    first, second = bot.calls[0][1]["message"], bot.calls[1][1]["message"]
    assert first[0].type == "reply" and first[0].data["id"] == "77"
    assert second.extract_plain_text() == "第二句"


def _stub_ai(monkeypatch, reply):
    async def gen(prompt, **kw):
        gen.prompt = prompt
        return reply

    async def cfg():
        return {"ai_system_prompt": "你是星潮"}

    monkeypatch.setattr(ai_plugin, "generate_text", gen)
    monkeypatch.setattr(ai_plugin, "ai_config", cfg)
    return gen


async def test_decide_and_act_reply_react_skip_and_limits(monkeypatch):
    await _feed(1, [(1, "甲", "这个报错怎么办"), (2, "乙", "我也遇到了")])
    bot = FakeBot()

    gen = _stub_ai(monkeypatch, '{"action":"reply","reply_to":1,"text":"看日志||贴一下"}')
    assert await pa.decide_and_act(bot, 1, 1, "help", "甲") == "reply"
    assert "#1 " in gen.prompt and "这个报错怎么办" in gen.prompt  # 带编号的上下文
    assert [n for n, _ in bot.calls] == ["send_group_msg"] * 2
    assert pa.attention_active(1, 1, 4)  # 回复后开启接话窗口

    bot = FakeBot()
    _stub_ai(monkeypatch, '{"action":"react","reply_to":2,"emoji":"赞"}')
    assert await pa.decide_and_act(bot, 1, 2, "discussion") == "react"
    assert bot.calls == [("set_msg_emoji_like", {"message_id": 101, "emoji_id": "76"})]

    bot = FakeBot(fail_react=True)
    assert await pa.decide_and_act(bot, 1, 2, "discussion") == "skip"  # 表情接口失败静默放弃

    bot = FakeBot()
    _stub_ai(monkeypatch, "SKIP")
    assert await pa.decide_and_act(bot, 1, 1, "discussion") == "skip" and bot.calls == []

    bot = FakeBot()
    _stub_ai(monkeypatch, '{"action":"reply","text":"嗯"}')
    # 滚动窗口里已有 1 条回复 + 1 条表情，上限设 2 → 拒绝
    assert await pa.decide_and_act(bot, 1, 1, "discussion", burst=2) == "skip" and bot.calls == []


async def test_decide_and_act_without_context_skips(monkeypatch):
    _stub_ai(monkeypatch, '{"action":"reply","text":"嗨"}')
    bot = FakeBot()
    assert await pa.decide_and_act(bot, 99, 1, "discussion") == "skip" and bot.calls == []
