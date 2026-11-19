"""入群申请审批：没答对验证题、但说明了真实来意的真人也该放行。"""

import json
from types import SimpleNamespace

import pytest

from src.plugins import request as rq


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    monkeypatch.setattr(rq, "get_store", lambda: store)
    rq._PENDING.clear()
    rq._PENDING_SEQ[0] = 0
    yield
    rq._PENDING.clear()


# ---------------------------------------------------------------- AI 判定

class _Completions:
    def __init__(self, payload):
        self.payload = payload

    async def create(self, **kw):
        _Completions.last_messages = kw.get("messages")
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _patch_ai(monkeypatch, payload):
    from src.plugins import ai as ai_plugin

    comp = _Completions(payload)

    class Client:
        chat = type("C", (), {"completions": comp})

    async def fake_client(kind="text"):
        return Client()

    async def fake_cfg(raw=False):
        return {"ai_model": "m"}

    monkeypatch.setattr(ai_plugin, "get_client", fake_client)
    monkeypatch.setattr(ai_plugin, "ai_config", fake_cfg)
    return comp


async def test_ai_approves_honest_answer(monkeypatch):
    _patch_ai(monkeypatch, {"decision": "approve", "kind": "answer", "reason": "答对了"})
    raw = await rq._ai_verdict(None, "GitHub是干什么的？", "代码托管平台")
    data = json.loads(raw)
    assert data["decision"] == "approve" and data["kind"] == "answer"


async def test_ai_approves_stated_intent(monkeypatch):
    """核心需求：答非所问，但说明了来意 → 通过。"""
    _patch_ai(monkeypatch, {"decision": "approve", "kind": "intent", "reason": "说明来意"})
    raw = await rq._ai_verdict(None, "GitHub是干什么的？", "我进来找人做个网站")
    data = json.loads(raw)
    assert data["decision"] == "approve" and data["kind"] == "intent"


async def test_ai_rejects_spam(monkeypatch):
    _patch_ai(monkeypatch, {"decision": "reject", "kind": "spam", "reason": "广告"})
    data = json.loads(await rq._ai_verdict(None, "GitHub是干什么的？", "加微信领取福利"))
    assert data["decision"] == "reject" and data["kind"] == "spam"


async def test_prompt_mentions_intent_not_a_reject_reason(monkeypatch):
    _patch_ai(monkeypatch, {"decision": "approve", "kind": "intent", "reason": "ok"})
    await rq._ai_verdict(None, "GitHub是干什么的？", "我进来找人做个网站")
    system = _Completions.last_messages[0]["content"]
    assert "说明了真实的入群目的" in system
    assert "没答对题不是拒绝的理由" in system
    assert "找人做网站" in system
    assert "spam" in system


async def test_bad_kind_is_tolerated(monkeypatch):
    _patch_ai(monkeypatch, {"decision": "approve", "kind": "nonsense", "reason": "x"})
    data = json.loads(await rq._ai_verdict(None, "Q", "A"))
    assert data["decision"] == "approve" and data["kind"] == ""


# ---------------------------------------------------------------- 规则兜底

def test_rule_verdict_answer_keywords():
    decision, kind = rq._rule_verdict("这是一个代码托管平台", "github,代码托管", "找人做,开发")
    assert decision == "approve" and kind == "answer"


def test_rule_verdict_intent_keywords():
    decision, kind = rq._rule_verdict("我进来找人做个网站", "github,代码托管", "找人做,做个网站")
    assert decision == "approve" and kind == "intent"


def test_rule_verdict_unknown_is_manual():
    decision, kind = rq._rule_verdict("随便写点什么", "github", "找人做")
    assert decision == "manual" and kind == ""


def test_rule_verdict_empty():
    assert rq._rule_verdict("", "github", "开发") == ("manual", "")


# ---------------------------------------------------------------- 默认配置

async def test_default_intent_keywords_present(store):
    cfg = await rq.join_config(555)
    assert "找人做" in cfg["join_intent_keywords"]
    assert "开发" in cfg["join_intent_keywords"]


# ---------------------------------------------------------------- 端到端

class _Bot:
    def __init__(self):
        self.calls = []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        return {}


def _event(comment="", gid=555, uid=10001):
    return SimpleNamespace(
        flag="f1", sub_type="add", group_id=gid, user_id=uid, comment=comment,
        request_type="group",
    )


async def _run(monkeypatch, store, comment, ai_payload=None, ai_down=False):
    await store.set_kv("join_mode", "ai")
    await store.set_kv("join_group_config", json.dumps({
        str(555): {"join_mode": "ai", "join_keywords": "github", "join_intent_keywords": "找人做,做个网站"}
    }))
    if ai_down:
        from src.plugins import ai as ai_plugin

        async def no_client(kind="text"):
            return None

        monkeypatch.setattr(ai_plugin, "get_client", no_client)
    elif ai_payload is not None:
        _patch_ai(monkeypatch, ai_payload)

    async def fake_notify(bot, text):
        bot.calls.append(("notify", {"text": text}))

    monkeypatch.setattr(rq, "_notify_superusers", fake_notify)
    bot = _Bot()
    await rq._handle_request(bot, _event(comment))
    return bot


async def test_e2e_stated_intent_is_approved(monkeypatch, store):
    bot = await _run(monkeypatch, store, "我进来找人做个网站",
                     ai_payload={"decision": "approve", "kind": "intent", "reason": "说明来意"})
    add = [c for c in bot.calls if c[0] == "set_group_add_request"]
    assert add and add[0][1]["approve"] is True


async def test_e2e_spam_is_rejected(monkeypatch, store):
    bot = await _run(monkeypatch, store, "加微信领取福利",
                     ai_payload={"decision": "reject", "kind": "spam", "reason": "广告"})
    add = [c for c in bot.calls if c[0] == "set_group_add_request"]
    assert add and add[0][1]["approve"] is False


async def test_e2e_rule_fallback_accepts_intent(monkeypatch, store):
    """AI 挂了也不能把说明来意的人挡掉——规则兜底要认来意关键词。"""
    bot = await _run(monkeypatch, store, "我进来找人做个网站", ai_down=True)
    add = [c for c in bot.calls if c[0] == "set_group_add_request"]
    assert add and add[0][1]["approve"] is True


async def test_e2e_unclear_goes_manual(monkeypatch, store):
    bot = await _run(monkeypatch, store, "呃",
                     ai_payload={"decision": "manual", "kind": "unclear", "reason": "看不出"})
    assert not [c for c in bot.calls if c[0] == "set_group_add_request"]
    assert rq._PENDING


async def test_e2e_notification_shows_reason(monkeypatch, store):
    bot = await _run(monkeypatch, store, "我进来找人做个网站",
                     ai_payload={"decision": "approve", "kind": "intent", "reason": "说明来意"})
    notes = [c[1]["text"] for c in bot.calls if c[0] == "notify"]
    assert any("说明了来意" in n for n in notes)
