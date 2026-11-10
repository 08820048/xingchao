import time

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import awareness as aw
from src.plugins import essence
from src.plugins import proactive as pa


class FakeBot:
    self_id = "1"

    def __init__(self, fail=None, rows=None):
        self.calls, self.fail, self.rows = [], fail, rows

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.rows


async def test_set_delete_essence_and_error_mapping():
    bot = FakeBot()
    assert await essence.set_essence(bot, 7) == (True, "已设为精华 ⭐")
    assert bot.calls[-1] == ("set_essence_msg", {"message_id": 7})
    assert (await essence.delete_essence(bot, 7))[0] is True
    ok, msg = await essence.set_essence(FakeBot(fail="no permission, not admin"), 7)
    assert not ok and "管理员" in msg


def test_format_essence_list():
    assert "还没有" in essence.format_essence_list([])
    rows = [{"sender_nick": "甲", "sender_time": 1731200000, "content": "第一条"},
            {"sender_nick": "乙", "content": [{"type": "text", "data": {"text": "文字段"}}]},
            {"sender_id": 3}]
    text = essence.format_essence_list(rows)
    assert "共 3 条" in text and "甲：第一条" in text and "乙：文字段" in text and "非文字消息" in text


async def test_list_essence_failure_is_reported():
    assert "失败" in await essence.list_essence(FakeBot(fail="boom"), 5)


def test_nomination_limits():
    essence._nominated.clear()
    essence._nominate_daily.clear()
    now = time.time()
    for mid in (1, 2, 3):
        assert essence.can_nominate(5, mid, now)
        essence.mark_nominated(5, mid, now)
    assert not essence.can_nominate(5, 4, now)      # 每群每天 3 次
    assert not essence.can_nominate(6, 1, now)      # 同一条只推荐一次
    assert essence.can_nominate(6, 9, now)          # 别的群不受影响
    assert "/精华 9 6" in essence.nomination_text(6, 9, "甲", "好内容", "总结得很清楚")


def test_parse_decision_nominate():
    ids = [10, 11]
    d = pa.parse_decision('{"action":"nominate","reply_to":2,"text":"总结得很清楚"}', ids)
    assert d == {"action": "nominate", "target": 11, "reason": "总结得很清楚"}
    assert pa.parse_decision('{"action":"nominate","text":"没指定消息"}', ids)["action"] == "skip"
    assert pa.parse_decision('{"action":"nominate","reply_to":1}', ids)["action"] == "skip"  # 没理由


async def test_decide_and_act_nominate_notifies_superusers_only(monkeypatch, store):
    from src.plugins import ai as ai_plugin
    from src.plugins import appeals, stickers

    monkeypatch.setattr(pa, "get_store", lambda: store)
    monkeypatch.setattr(pa, "PACING", False)

    async def no_lib(bot, **kw):
        return 0

    async def cfg():
        return dict(aw.DEFAULTS)

    async def ai_cfg():
        return {"ai_system_prompt": "x"}

    async def gen(prompt, **kw):
        return '{"action":"nominate","reply_to":1,"text":"经验总结很完整"}'

    monkeypatch.setattr(stickers, "ensure_library", no_lib)
    monkeypatch.setattr(aw, "config", cfg)
    monkeypatch.setattr(ai_plugin, "ai_config", ai_cfg)
    monkeypatch.setattr(ai_plugin, "generate_text", gen)
    monkeypatch.setattr(appeals, "_superuser_ids", lambda: {100})
    essence._nominated.clear()
    essence._nominate_daily.clear()
    aw.clear_group()
    await aw.record(5, 11, "甲", 700, Message([MessageSegment.text("装环境的完整步骤……")]), now=time.time())
    bot = FakeBot()
    assert await pa.decide_and_act(bot, 5, 11, "discussion") == "nominate"
    assert [n for n, _ in bot.calls] == ["send_private_msg"]  # 群里不发任何东西
    assert "/精华 700 5" in bot.calls[0][1]["message"]
    assert await pa.decide_and_act(FakeBot(), 5, 11, "discussion") == "skip"  # 同一条不重复推荐
    aw.clear_group()
