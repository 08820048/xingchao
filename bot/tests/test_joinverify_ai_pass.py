"""入群验证的 AI 放宽通道：答不上算术题、但确实是真人说明来意时放行。"""

import time
from types import SimpleNamespace

import pytest

from src.plugins import joinverify as jv


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    monkeypatch.setattr(jv, "get_store", lambda: store)
    jv._pending.clear()
    yield
    jv._pending.clear()


def _pending(**over):
    base = dict(answer=7, deadline=time.time() + 300, question="3+4")
    base.update(over)
    return jv.Pending(**base)


# ---------------------------------------------------------------- 什么时候值得送 AI

@pytest.mark.parametrize("text", [
    "我进来找人做个网站",
    "想找大佬帮我看看代码",
    "你好，我是来请教问题的",
    "I joined to find a developer",
])
def test_natural_language_detected(text):
    assert jv.looks_like_natural_text(text) is True


@pytest.mark.parametrize("text", [
    "", "   ", "0000", "12", "9-9", "e^2", "1+2+3", "哈哈哈", "可以",
])
def test_non_natural_text_not_sent_to_ai(text):
    assert jv.looks_like_natural_text(text) is False


# ---------------------------------------------------------------- 放行 / 拒绝

async def test_ai_pass_allows_genuine_person(monkeypatch):
    async def fake_judge(text):
        return True, "说明了来意"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending()
    ok, reason = await jv.try_ai_pass(555, p, "我进来找人做个网站")
    assert ok is True and reason == "说明了来意"
    assert p.ai_tries == 1


async def test_ai_rejects_like_bot(monkeypatch):
    async def fake_judge(text):
        return False, "像群发广告"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    ok, _ = await jv.try_ai_pass(555, _pending(), "加微信领取福利")
    assert ok is False


async def test_ai_not_called_for_numbers(monkeypatch):
    calls = []

    async def fake_judge(text):
        calls.append(text)
        return True, "x"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    ok, reason = await jv.try_ai_pass(555, _pending(), "12")
    assert ok is False and reason == "不像人话"
    assert calls == []          # 纯数字根本不问 AI


async def test_ai_disabled_by_config(monkeypatch):
    calls = []

    async def fake_judge(text):
        calls.append(text)
        return True, "x"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    ok, reason = await jv.try_ai_pass(555, _pending(ai_pass_enabled=False), "我进来找人做网站")
    assert ok is False and reason == "未开启"
    assert calls == []


async def test_ai_per_user_try_cap(monkeypatch):
    async def fake_judge(text):
        return False, "不像真人"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending()
    for _ in range(jv.AI_PASS_MAX_TRIES):
        await jv.try_ai_pass(555, p, "随便说点什么呀")
    ok, reason = await jv.try_ai_pass(555, p, "再说一句试试")
    assert ok is False and "用满" in reason


async def test_ai_daily_budget(store, monkeypatch):
    async def fake_judge(text):
        return True, "ok"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending(ai_daily_limit=1)
    assert (await jv.try_ai_pass(555, p, "我想进来学习一下"))[0] is True
    p2 = _pending(ai_daily_limit=1)
    ok, reason = await jv.try_ai_pass(555, p2, "我也想进来学习一下")
    assert ok is False and "额度" in reason
    # 另一个群有自己的额度
    assert (await jv.try_ai_pass(666, _pending(ai_daily_limit=1), "我来找人做网站"))[0] is True


async def test_budget_zero_disables(store):
    ok, reason = await jv.try_ai_pass(555, _pending(ai_daily_limit=0), "我进来找人做网站")
    assert ok is False and "额度" in reason


# ---------------------------------------------------------------- AI 调用与降级

async def test_judge_returns_false_when_ai_missing(monkeypatch):
    from src.plugins import ai as ai_plugin

    async def no_client(kind="text"):
        return None

    monkeypatch.setattr(ai_plugin, "get_client", no_client)
    ok, reason = await jv.ai_judge_genuine("我进来找人做网站")
    assert ok is False and "AI 不可用" in reason


async def test_judge_parses_json(monkeypatch):
    from src.plugins import ai as ai_plugin

    class Completions:
        @staticmethod
        async def create(**kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"pass": true, "reason": "说明来意"}'))])

    class Client:
        chat = type("C", (), {"completions": Completions})

    async def fake_client(kind="text"):
        return Client()

    async def fake_cfg(raw=False):
        return {"ai_model": "m"}

    monkeypatch.setattr(ai_plugin, "get_client", fake_client)
    monkeypatch.setattr(ai_plugin, "ai_config", fake_cfg)
    ok, reason = await jv.ai_judge_genuine("我进来找人做个网站")
    assert ok is True and reason == "说明来意"


async def test_judge_is_fail_closed(monkeypatch):
    from src.plugins import ai as ai_plugin

    class Completions:
        @staticmethod
        async def create(**kw):
            raise RuntimeError("网络挂了")

    class Client:
        chat = type("C", (), {"completions": Completions})

    async def fake_client(kind="text"):
        return Client()

    async def fake_cfg(raw=False):
        return {"ai_model": "m"}

    monkeypatch.setattr(ai_plugin, "get_client", fake_client)
    monkeypatch.setattr(ai_plugin, "ai_config", fake_cfg)
    ok, reason = await jv.ai_judge_genuine("我进来找人做个网站")
    assert ok is False and "调用失败" in reason      # 失败一律不放行


async def test_judge_unparsable_reply_is_rejected(monkeypatch):
    from src.plugins import ai as ai_plugin

    class Completions:
        @staticmethod
        async def create(**kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="我觉得可以吧"))])

    class Client:
        chat = type("C", (), {"completions": Completions})

    async def fake_client(kind="text"):
        return Client()

    async def fake_cfg(raw=False):
        return {"ai_model": "m"}

    monkeypatch.setattr(ai_plugin, "get_client", fake_client)
    monkeypatch.setattr(ai_plugin, "ai_config", fake_cfg)
    ok, reason = await jv.ai_judge_genuine("我进来找人做网站")
    assert ok is False and "不可解析" in reason


# ---------------------------------------------------------------- 配置

async def test_config_defaults_and_toggle(store):
    c = await jv._cfg()
    assert c["ai_pass"] is True            # 默认开启
    msg = await jv.set_config(ai_pass=False)
    assert "AI 复核：关闭" in msg
    assert (await jv._cfg())["ai_pass"] is False
    msg = await jv.set_config(ai_pass=True, ai_daily_limit=5)
    assert "AI 复核：开启" in msg and (await jv._cfg())["ai_daily_limit"] == 5


async def test_daily_limit_is_clamped(store):
    await jv.set_config(ai_daily_limit=999999)
    assert (await jv._cfg())["ai_daily_limit"] == 10000
    await jv.set_config(ai_daily_limit=-5)
    assert (await jv._cfg())["ai_daily_limit"] == 0


# ---------------------------------------------------------------- 完整答题流程

class _Bot:
    self_id = "1"

    def __init__(self):
        self.sent = []

    async def call_api(self, name, **kw):
        self.sent.append((name, kw))
        return {"message_id": len(self.sent)}


def _event(uid=10001, gid=555, text=""):
    return SimpleNamespace(
        group_id=gid, user_id=uid,
        message=SimpleNamespace(extract_plain_text=lambda: text),
    )


async def test_genuine_intent_gets_through(monkeypatch, store):
    """答不上题、但说明来意 → 放行，且不计答错次数。"""
    async def fake_judge(text):
        return True, "说明来意"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending(answer=7)
    jv._pending[(555, 10001)] = p
    bot = _Bot()
    await jv.handle_answer(bot, _event(text="我进来找人做个网站"))
    assert (555, 10001) not in jv._pending       # 已通过
    assert p.attempts == 0                       # 没有记答错
    joined = " ".join(str(m) for _n, kw in bot.sent for m in [kw.get("message", "")])
    assert "验证通过" in joined


async def test_correct_answer_still_fast_path(monkeypatch):
    """答对时不该惊动 AI。"""
    calls = []

    async def fake_judge(text):
        calls.append(text)
        return False, "x"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    jv._pending[(555, 10001)] = _pending(answer=7)
    bot = _Bot()
    await jv.handle_answer(bot, _event(text="7"))
    assert (555, 10001) not in jv._pending
    assert calls == []


async def test_bot_like_message_is_not_passed(monkeypatch, store):
    """AI 不放行时维持原判：不放进来，等他超时。

    注意这里不累计答错次数——消息里没有数字，按既有规则「纯聊天不算答错」，
    这类人不该被 3 次机会踢掉，而是走超时流程。
    """
    async def fake_judge(text):
        return False, "像广告"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending(answer=7)
    jv._pending[(555, 10001)] = p
    await jv.handle_answer(_Bot(), _event(text="加我微信领取福利"))
    assert (555, 10001) in jv._pending
    assert p.ai_tries == 1          # 确实问过 AI
    assert p.attempts == 0          # 但没有数字，不计答错


async def test_pure_number_wrong_still_counts(monkeypatch):
    """纯数字错答不问 AI，直接计次。"""
    calls = []

    async def fake_judge(text):
        calls.append(text)
        return True, "x"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending(answer=7)
    jv._pending[(555, 10001)] = p
    await jv.handle_answer(_Bot(), _event(text="12"))
    assert p.attempts == 1
    assert calls == []


async def test_ai_disabled_falls_back_to_strict(monkeypatch, store):
    async def fake_judge(text):
        return True, "说明来意"

    monkeypatch.setattr(jv, "ai_judge_genuine", fake_judge)
    p = _pending(answer=7, ai_pass_enabled=False)
    jv._pending[(555, 10001)] = p
    await jv.handle_answer(_Bot(), _event(text="我进来找人做个网站"))
    assert (555, 10001) in jv._pending     # 没被放行
    assert p.ai_tries == 0                 # 开关关掉后连 AI 都不问
    assert p.attempts == 0                 # 没有数字，不计答错（走超时）
