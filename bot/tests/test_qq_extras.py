import time
from datetime import datetime

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import groupextras as ge
from src.plugins import groupsign as gs
from src.plugins import nctools
from src.plugins import onlinestatus as os_
from src.plugins import urlsafety as us


class FakeBot:
    self_id = "1"

    def __init__(self, behaviors=None):
        self.behaviors, self.calls = behaviors or {}, []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        b = self.behaviors.get(name, {})
        if isinstance(b, Exception):
            raise b
        return b


# ---- @全体
async def test_at_all_allowed():
    assert await nctools.at_all_allowed(FakeBot({"get_group_at_all_remain": {"can_at_all": True, "remain_at_all_count_for_group": 3, "remain_at_all_count_for_uin": 5}}), 1) == (True, "")
    ok, why = await nctools.at_all_allowed(FakeBot({"get_group_at_all_remain": {"can_at_all": True, "remain_at_all_count_for_group": 0}}), 1)
    assert not ok and "次数已用完" in why
    ok, why = await nctools.at_all_allowed(FakeBot({"get_group_at_all_remain": {"can_at_all": False}}), 1)
    assert not ok and "管理员" in why
    assert (await nctools.at_all_allowed(FakeBot({"get_group_at_all_remain": RuntimeError("x")}), 1))[0] is True  # 未知则照常尝试


async def test_scheduler_degrades_when_at_all_unavailable(monkeypatch):
    from src.plugins import appeals, scheduler

    monkeypatch.setattr(appeals, "_superuser_ids", lambda: {100})
    bot = FakeBot({"get_group_at_all_remain": {"can_at_all": True, "remain_at_all_count_for_group": 0}})
    await scheduler._fire(bot, {"id": 3, "group_id": 5, "message": "开会", "at_all": True})
    sends = [kw for n, kw in bot.calls if n == "send_group_msg"]
    msg = sends[0]["message"]
    segs = [msg] if isinstance(msg, MessageSegment) else list(msg)
    assert len(sends) == 1 and all(s.type != "at" for s in segs) and segs[0].data["text"] == "开会"  # 没有 @全体
    assert any(n == "send_private_msg" and "无法 @全体" in kw["message"] for n, kw in bot.calls)


# ---- 翻译
def test_parse_translation():
    assert nctools.parse_translation({"words": ["你好"]}, 1) == ["你好"]
    assert nctools.parse_translation(["甲", "乙"], 2) == ["甲", "乙"]
    assert nctools.parse_translation(["甲"], 2) is None and nctools.parse_translation(None, 1) is None


# ---- 合并转发
def test_forward_to_text():
    raw = {"messages": [
        {"sender": {"nickname": "小明"}, "content": [{"type": "text", "data": {"text": "大家好"}}, {"type": "image", "data": {"file": "a"}}]},
        {"sender": {"nickname": "小红", "card": "红红"}, "message": "你好呀"},
        {"sender": {"nickname": "空"}, "content": []},
    ]}
    assert nctools.forward_to_text(raw) == "小明: 大家好 [图片]\n红红: 你好呀"
    long = {"messages": [{"sender": {"nickname": "甲"}, "content": [{"type": "text", "data": {"text": "x" * 110}}]}] * 15}
    assert nctools.forward_to_text(long).endswith("…") and len(nctools.forward_to_text(long)) <= 801


async def test_read_forward_tries_param_variants():
    class B:
        def __init__(self):
            self.calls = []

        async def call_api(self, name, **kw):
            self.calls.append(kw)
            if "message_id" in kw:
                raise RuntimeError("bad param")
            return {"messages": [{"sender": {"nickname": "甲"}, "content": "内容"}]}

    b = B()
    assert await nctools.read_forward(b, "fw1") == "甲: 内容" and len(b.calls) == 2
    assert await nctools.read_forward(FakeBot({"get_forward_msg": RuntimeError("x")}), "fw1") == ""


# ---- 链接安全
def test_url_level_and_extract():
    assert [nctools.url_level({"level": v}) for v in (1, 2, 3, "3", None)] == ["safe", "unknown", "danger", "danger", "unknown"]
    assert us.extract_urls("看 https://a.com/x, 还有 https://b.com。https://a.com/x") == ["https://a.com/x", "https://b.com"]


async def test_check_url_cache_and_unsupported(monkeypatch):
    nctools._url_cache.clear()
    nctools._url_unsupported = False
    bot = FakeBot({"check_url_safely": {"level": 3}})
    now = time.time()
    assert await nctools.check_url(bot, "https://bad.example", now) == "danger"
    assert await nctools.check_url(bot, "https://bad.example", now + 10) == "danger" and len(bot.calls) == 1
    assert nctools.known_dangerous("https://bad.example", now + 10) and not nctools.known_dangerous("https://ok.example")
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    bot2 = FakeBot({"check_url_safely": ActionFailed(retcode=1404, message="不支持的Api")})
    assert await nctools.check_url(bot2, "https://x.example") == "unknown"
    assert await nctools.check_url(bot2, "https://y.example") == "unknown" and len(bot2.calls) == 1  # 停用后不再调用
    nctools._url_unsupported = False
    nctools._url_cache.clear()


def test_warning_text_truncates():
    assert "被 QQ 安全中心标记" in us.warning_text("https://a.com") and "…" in us.warning_text("https://a.com/" + "x" * 100)


# ---- 群签到
def test_sign_minute_window_and_stability():
    for gid in (1, 123456, 987654321):
        m = gs.sign_minute(gid, "2026-11-11")
        assert 8 * 60 <= m < 10 * 60 and m == gs.sign_minute(gid, "2026-11-11")
    assert len({gs.sign_minute(g, "2026-11-11") for g in range(1, 40)}) > 5  # 不同群错开


async def test_group_sign_tick_once_per_day(store, monkeypatch):
    monkeypatch.setattr(gs, "get_store", lambda: store)
    monkeypatch.setattr(gs, "merged_whitelist", lambda: {5})
    monkeypatch.setattr(gs, "disabled_groups", lambda: set())
    bot = FakeBot()
    early = datetime(2026, 11, 11, 7, 0)
    late = datetime(2026, 11, 11, 11, 0)
    assert await gs.tick(bot, early) == []
    assert await gs.tick(bot, late) == [5] and bot.calls == [("set_group_sign", {"group_id": 5})]
    assert await gs.tick(bot, late) == []  # 当天不再重复
    await store.set_kv("group_sign_enabled", "false")
    assert await gs.tick(bot, datetime(2026, 11, 12, 11, 0)) == []
    # 主接口失败时回退备用接口
    await store.set_kv("group_sign_enabled", "true")
    bot2 = FakeBot({"set_group_sign": RuntimeError("x")})
    assert await gs.tick(bot2, datetime(2026, 11, 13, 11, 0)) == [5] and bot2.calls[-1][0] == "send_group_sign"


# ---- 在线状态
def test_pick_wording():
    at = lambda h: datetime(2026, 11, 11, h, 0)  # noqa: E731
    assert os_.pick_wording(at(2), 0) == "夜深了·陪聊中" and os_.pick_wording(at(7), 0) == "刚醒·加载中"
    assert os_.pick_wording(at(13), 99) == "午饭时间"
    assert os_.pick_wording(at(15), 50) == "群里热闹·吃瓜" and os_.pick_wording(at(15), 0) == "潜水观察中"
    assert os_.pick_wording(at(20), 0) == "在线·陪聊中"
    assert all(len(os_.pick_wording(at(h), n)) <= 10 for h in range(24) for n in (0, 99))


async def test_online_status_default_off_and_updates_on_change(store, monkeypatch):
    monkeypatch.setattr(os_, "get_store", lambda: store)
    monkeypatch.setattr(os_, "recent_activity", lambda *a, **k: 0)
    os_._last_wording = ""
    bot = FakeBot()
    assert await os_.update(bot, datetime(2026, 11, 11, 15)) is None and bot.calls == []  # 默认关闭
    await store.set_kv("online_status_enabled", "true")
    assert await os_.update(bot, datetime(2026, 11, 11, 15)) == "潜水观察中"
    assert await os_.update(bot, datetime(2026, 11, 11, 16)) is None and len(bot.calls) == 1  # 没变化不重复调用
    assert bot.calls[0][1]["wording"] == "潜水观察中"
    os_._last_wording = ""


# ---- 群荣誉 / 名片 / 头衔
def test_format_honor():
    r = {"current_talkative": {"user_id": 1, "nickname": "小明", "day_count": 3},
         "performer_list": [{"nickname": "小红"}, {"nickname": "小刚"}], "legend_list": []}
    text = ge.format_honor(r)
    assert "🐲 龙王：小明（3天）" in text and "🔥 群聊之火：小红、小刚" in text and "炽焰" not in text
    assert "暂时没有" in ge.format_honor({}) and "没取到" in ge.format_honor(None)


async def test_card_and_title():
    bot = FakeBot()
    assert (await ge.set_card(bot, 5, 11, "  新  名片 "))[0] and bot.calls[-1][1]["card"] == "新 名片"
    ok, msg = await ge.set_title(bot, 5, 11, "七个字的头衔啊啊")
    assert not ok and "6" in msg
    ok, msg = await ge.set_title(bot, 5, 11, "龙王")
    assert ok and bot.calls[-1][1] == {"group_id": 5, "user_id": 11, "special_title": "龙王", "duration": -1}
    ok, msg = await ge.set_card(FakeBot({"set_group_card": RuntimeError("no permission")}), 5, 11, "x")
    assert not ok and "管理员" in msg
    assert ge._split(Message([MessageSegment.at(77), MessageSegment.text(" 新头衔 ")])) == (77, "新头衔")
