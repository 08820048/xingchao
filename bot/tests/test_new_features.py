"""联网工具、个人提醒、入群验证、投票、绘图额度、红包识别。"""

import random
import time
from datetime import datetime

import pytest

from src.plugins import imagegen, joinverify, polls, redpacket, reminders, webtools

NOW = datetime(2026, 11, 11, 10, 0)  # 周三


# ---------------------------------------------------------------- 提醒时间解析

@pytest.mark.parametrize("text,due,content", [
    ("10分钟后 喝水", datetime(2026, 11, 11, 10, 10), "喝水"),
    ("2小时后开会", datetime(2026, 11, 11, 12, 0), "开会"),
    ("明天8点 开会", datetime(2026, 11, 12, 8, 0), "开会"),
    ("今晚8点半 吃饭", datetime(2026, 11, 11, 20, 30), "吃饭"),
    ("20:30 打卡", datetime(2026, 11, 11, 20, 30), "打卡"),
    ("9:00 起床", datetime(2026, 11, 12, 9, 0), "起床"),  # 钟点已过 → 明天
    ("后天 9:00 交报告", datetime(2026, 11, 13, 9, 0), "交报告"),
    ("2026-12-01 08:00 生日", datetime(2026, 12, 1, 8, 0), "生日"),
    ("明早7点 跑步", datetime(2026, 11, 12, 7, 0), "跑步"),
    ("五分钟后提醒我领红包", datetime(2026, 11, 11, 10, 5), "提醒我领红包"),
    ("五分钟后 提醒我领一个红包", datetime(2026, 11, 11, 10, 5), "提醒我领一个红包"),  # 内容里的中文数字不被改动
    ("十分钟后 喝水", datetime(2026, 11, 11, 10, 10), "喝水"),
    ("二十三分钟后 x", datetime(2026, 11, 11, 10, 23), "x"),
    ("半小时后 喝水", datetime(2026, 11, 11, 10, 30), "喝水"),
    ("两小时后 开会", datetime(2026, 11, 11, 12, 0), "开会"),
    ("一个半小时后 x", datetime(2026, 11, 11, 11, 30), "x"),
    ("三天后 交作业", datetime(2026, 11, 14, 10, 0), "交作业"),
    ("明天下午三点 开会", datetime(2026, 11, 12, 15, 0), "开会"),
    ("今天晚上八点半 吃饭", datetime(2026, 11, 11, 20, 30), "吃饭"),
    ("明天早上八点十分 x", datetime(2026, 11, 12, 8, 10), "x"),
    ("中午一点 午休结束", datetime(2026, 11, 11, 13, 0), "午休结束"),  # 现在 10:00，13:00 就是今天
])
def test_parse_when(text, due, content):
    assert reminders.parse_when(text, NOW) == (due, content)


@pytest.mark.parametrize("text", ["随便说说", "25点 x", "明天 没写时间"])
def test_parse_when_rejects(text):
    assert reminders.parse_when(text, NOW) is None


@pytest.fixture
def kv(store, monkeypatch):
    monkeypatch.setattr(reminders, "get_store", lambda: store)
    return store


class _Bot:
    self_id = "1"

    def __init__(self):
        self.calls = []

    async def call_api(self, api, **kw):
        self.calls.append((api, kw))


async def test_reminder_create_limits_list_cancel_tick(kv):
    soon = datetime.fromtimestamp(time.time() + 10)
    ok, msg = await reminders.create(1, 9, soon, "x")
    assert not ok and "1 分钟" in msg
    due = datetime.fromtimestamp(time.time() + 3600)
    assert (await reminders.create(1, 9, due, ""))[0] is False
    for i in range(reminders.MAX_PER_USER):
        assert (await reminders.create(1, 9, due, f"事{i}"))[0]
    ok, msg = await reminders.create(1, 9, due, "多一条")
    assert not ok and "取消" in msg
    assert "事0" in await reminders.list_text(1)
    items = await kv.list_reminders(1)
    assert (await reminders.cancel(2, items[0]["id"]))[0] is False  # 别人不能取消
    assert (await reminders.cancel(1, items[0]["id"]))[0] is True

    bot = _Bot()
    assert await reminders.tick(bot, int(time.time())) == 0
    n = await reminders.tick(bot, int(time.time()) + 7200)
    assert n == reminders.MAX_PER_USER - 1
    assert bot.calls[0][0] == "send_group_msg" and "迟到" in str(bot.calls[0][1]["message"])
    assert await kv.list_reminders() == []


async def test_private_reminder_goes_private(kv):
    due = datetime.fromtimestamp(time.time() + 3600)
    await reminders.create(5, None, due, "私聊提醒")
    bot = _Bot()
    await reminders.tick(bot, int(time.time()) + 3600)
    assert bot.calls[0][0] == "send_private_msg"


# ---------------------------------------------------------------- 联网工具

DDG = '''
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">示例 <b>标题</b></a>
<a class="result__snippet" href="x">这是 <b>摘要</b> 内容</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">广告</a></div>
<div class="result"><a class="result__a" href="https://b.org/">第二条</a>
<a class="result__snippet" href="x">摘要二</a></div>
'''


def test_parse_ddg_unwraps_and_skips_ads():
    res = webtools.parse_ddg(DDG)
    assert [r["url"] for r in res] == ["https://example.com/a", "https://b.org/"]
    assert res[0]["title"] == "示例 标题" and res[0]["snippet"] == "这是 摘要 内容"
    assert "1. 示例 标题" in webtools.format_results(res)


async def test_search_requires_provider_config(monkeypatch):
    class Cfg:
        xingchao_search_provider = "brave"
        xingchao_search_api_key = ""
        xingchao_search_url = ""

    monkeypatch.setattr(webtools, "get_config", lambda: Cfg)
    res, note = await webtools.search("x")
    assert res is None and "API_KEY" in note
    Cfg.xingchao_search_provider = "searxng"
    res, note = await webtools.search("x")
    assert res is None and "SEARCH_URL" in note
    assert (await webtools.search("   "))[0] is None


async def test_search_daily_limit(monkeypatch):
    monkeypatch.setattr(webtools, "_daily", (datetime.now().strftime("%Y-%m-%d"), webtools.SEARCH_DAILY_LIMIT))
    res, note = await webtools.search("x")
    assert res is None and "用完" in note


def test_format_weather():
    data = {
        "current_condition": [{"temp_C": "12", "FeelsLikeC": "10", "humidity": "60", "windspeedKmph": "9",
                               "winddir16Point": "N", "lang_zh": [{"value": "晴"}]}],
        "weather": [{"date": "2026-11-11", "mintempC": "5", "maxtempC": "14",
                     "hourly": [{}, {"weatherDesc": [{"value": "Sunny"}]}, {}]}],
    }
    text = webtools.format_weather("北京", data)
    assert "晴，12°C" in text and "今天（2026-11-11）：Sunny，5~14°C" in text


async def test_rate_validates_input():
    assert "三位代码" in await webtools.rate_text("美元", "CNY")
    assert "金额" in await webtools.rate_text("USD", "CNY", -1)


# ---------------------------------------------------------------- 入群验证

def test_question_and_answer():
    for seed in range(50):
        q, ans = joinverify.make_question(random.Random(seed))
        a, op, b = (int(q[0]), q[1], int(q[2]))
        assert ans == (a + b if op == "+" else a - b) and 0 <= ans <= 18
    assert joinverify.check_answer("答案是 11 哦", 11)
    assert not joinverify.check_answer("111", 11) and not joinverify.check_answer("你好", 11)


class _Msg:
    def __init__(self, text):
        self._t = text

    def extract_plain_text(self):
        return self._t


class _Ev:
    def __init__(self, group_id, user_id, text):
        self.group_id, self.user_id, self.message = group_id, user_id, _Msg(text)


def _group_texts(bot):
    return [str(c[1]["message"]) for c in bot.calls if c[0] == "send_group_msg"]


@pytest.fixture
def jv(store, monkeypatch):
    monkeypatch.setattr(joinverify, "get_store", lambda: store)
    sent = []

    async def fake_notify(bot, text, exclude=None):
        sent.append(text)

    from src.plugins import appeals
    monkeypatch.setattr(appeals, "notify_superusers", fake_notify)
    joinverify._pending.clear()
    yield sent
    joinverify._pending.clear()


async def test_timeout_announces_in_group_before_punishing(jv):
    """超时：先在群里 @ 公示并给宽限，宽限过了才踢；公示之前不能动手。"""
    await joinverify.set_config(action="kick")
    now = time.time()
    joinverify._pending[(1, 12)] = joinverify.Pending(5, now - 1, "2+3")
    joinverify._pending[(1, 13)] = joinverify.Pending(5, now + 100, "2+3")
    bot = _Bot()
    assert await joinverify.handle_timeouts(bot, now) == 1
    texts = _group_texts(bot)
    assert len(texts) == 1 and "未通过" in texts[0] and "超时" in texts[0] and "移出群聊" in texts[0]
    assert not any(c[0] == "set_group_kick" for c in bot.calls)  # 公示阶段不踢
    assert jv == []  # 也还没通知超管

    assert await joinverify.handle_timeouts(bot, now + 5) == 0  # 宽限没到，什么都不做
    assert not any(c[0] == "set_group_kick" for c in bot.calls)

    assert await joinverify.handle_timeouts(bot, now + joinverify.GRACE_SECONDS + 1) == 1
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1 and kicks[0][1]["user_id"] == 12
    assert (1, 12) not in joinverify._pending and (1, 13) in joinverify._pending
    assert "已移出群聊" in _group_texts(bot)[-1] and "移出群" in jv[0]


async def test_notify_mode_announces_then_notifies_superusers(jv):
    now = time.time()
    joinverify._pending[(1, 10)] = joinverify.Pending(5, now - 1, "2+3")
    bot = _Bot()
    await joinverify.handle_timeouts(bot, now)
    assert "交由管理员处理" in _group_texts(bot)[0] and jv == []
    await joinverify.handle_timeouts(bot, now + joinverify.GRACE_SECONDS + 1)
    assert "未通过" in jv[0] and not any(c[0] == "set_group_kick" for c in bot.calls)


async def test_answer_during_grace_still_passes(jv):
    await joinverify.set_config(action="kick")
    now = time.time()
    joinverify._pending[(1, 12)] = joinverify.Pending(5, now - 1, "2+3")
    bot = _Bot()
    await joinverify.handle_timeouts(bot, now)
    await joinverify.handle_answer(bot, _Ev(1, 12, "5"))
    assert (1, 12) not in joinverify._pending and "验证通过" in _group_texts(bot)[-1]
    await joinverify.handle_timeouts(bot, now + 999)
    assert not any(c[0] == "set_group_kick" for c in bot.calls)


async def test_wrong_answers_warn_then_fail_after_max_attempts(jv):
    await joinverify.set_config(action="kick")
    joinverify._pending[(1, 12)] = joinverify.Pending(5, time.time() + 300, "2+3")
    bot = _Bot()
    await joinverify.handle_answer(bot, _Ev(1, 12, "大家好"))  # 没有数字：不算答错
    assert joinverify._pending[(1, 12)].attempts == 0 and _group_texts(bot) == []

    await joinverify.handle_answer(bot, _Ev(1, 12, "6"))
    assert "答案不对" in _group_texts(bot)[-1] and "还有 2 次" in _group_texts(bot)[-1]
    await joinverify.handle_answer(bot, _Ev(1, 12, "7"))
    assert "还有 1 次" in _group_texts(bot)[-1]
    await joinverify.handle_answer(bot, _Ev(1, 12, "8"))  # 第三次错：公示，但还没踢
    assert "答错了 3 次" in _group_texts(bot)[-1] and "移出群聊" in _group_texts(bot)[-1]
    assert not any(c[0] == "set_group_kick" for c in bot.calls)
    p = joinverify._pending[(1, 12)]
    await joinverify.handle_timeouts(bot, p.punish_at + 1)
    assert any(c[0] == "set_group_kick" for c in bot.calls)


async def test_kick_failure_tells_member_and_admins(jv):
    await joinverify.set_config(action="kick")
    now = time.time()
    joinverify._pending[(1, 12)] = joinverify.Pending(5, now - 1, "2+3")

    class _Fail(_Bot):
        async def call_api(self, name, **kw):
            if name == "set_group_kick":
                raise RuntimeError("no permission")
            return await super().call_api(name, **kw)

    bot = _Fail()
    await joinverify.handle_timeouts(bot, now)
    await joinverify.handle_timeouts(bot, now + joinverify.GRACE_SECONDS + 1)
    assert "踢出失败" in jv[0] and "已通知管理员" in _group_texts(bot)[-1]


async def test_join_verify_config_clamped(store, monkeypatch):
    monkeypatch.setattr(joinverify, "get_store", lambda: store)
    assert "时限 60 分钟" in await joinverify.set_config(minutes=999)
    assert "开启" in await joinverify.set_config(enabled=True)


# ---------------------------------------------------------------- 投票

def test_poll_flow():
    polls._polls.clear()
    ok, msg = polls.create_poll(1, 100, "吃啥", ["火锅", "烧烤", "火锅"])
    assert ok and msg.count("火锅") == 1 and "1. 火锅" in msg
    assert not polls.create_poll(1, 100, "再来", ["a", "b"])[0]  # 同群只能一个
    assert not polls.create_poll(2, 1, "x", ["只有一个"])[0]
    assert polls.vote(1, 7, 1)[0] and polls.vote(1, 8, 2)[0] and polls.vote(1, 9, 2)[0]
    assert "改票" in polls.vote(1, 7, 2)[1]
    assert not polls.vote(1, 7, 5)[0] and not polls.vote(3, 7, 1)[0]
    assert not polls.end_poll(1, 55)[0]  # 非发起人
    ok, text = polls.end_poll(1, 100)
    assert ok and "烧烤：3 票（100%） 👑" in text and 1 not in polls._polls


async def test_poll_expiry_announced():
    polls._polls.clear()
    polls.create_poll(1, 1, "q", ["a", "b"], minutes=1, now=1000.0)
    bot = _Bot()
    assert await polls.announce_expired(bot, now=1030.0) == 0
    assert await polls.announce_expired(bot, now=1100.0) == 1
    assert "投票时间到" in bot.calls[0][1]["message"] and not polls._polls


# ---------------------------------------------------------------- 绘图额度

def test_image_quota(monkeypatch):
    imagegen._user_daily.clear()
    imagegen._last.clear()
    monkeypatch.setattr(imagegen, "_global_daily", ("", 0))
    assert imagegen.check_quota(1) is None
    imagegen._consume(1)
    assert "稍等" in imagegen.check_quota(1)
    imagegen._last.clear()
    imagegen._consume(1); imagegen._last.clear(); imagegen._consume(1); imagegen._last.clear()
    assert "3 张" in imagegen.check_quota(1)
    assert imagegen.check_quota(2) is None


async def test_image_disabled_without_model(store, monkeypatch):
    monkeypatch.setattr(imagegen, "get_store", lambda: store)
    seg, tip = await imagegen.generate(1, "一只猫")
    assert seg is None and "image_model" in tip
    assert "想画什么" in (await imagegen.generate(1, "  "))[1]


# ---------------------------------------------------------------- 红包识别

def test_red_packet_detection():
    assert redpacket.looks_like_red_packet("[QQ红包]恭喜发财", [("text", {"text": "[QQ红包]恭喜发财"})])
    assert redpacket.looks_like_red_packet("", [("json", {"data": '{"walletElement":{}}'})])
    assert redpacket.looks_like_red_packet("x", [("json", {"data": "QQ红包"})])
    assert not redpacket.looks_like_red_packet("发个红包吧", [("text", {"text": "发个红包吧"})])
    assert not redpacket.looks_like_red_packet("红包", [("text", {"text": "红包"}), ("image", {"file": "a.jpg"})])


async def test_ddg_challenge_page_reported_as_blocked(monkeypatch):
    class Cfg:
        xingchao_search_provider = "duckduckgo"
        xingchao_search_api_key = ""
        xingchao_search_url = ""

    class Resp:
        status_code = 202
        text = "<html>anomaly-modal challenge</html>"

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(webtools, "get_config", lambda: Cfg)
    monkeypatch.setattr(webtools.httpx, "AsyncClient", Client)
    monkeypatch.setattr(webtools, "_daily", ("", 0))
    res, note = await webtools.search("python")
    assert res is None and "拦截" in note and "brave" in note
