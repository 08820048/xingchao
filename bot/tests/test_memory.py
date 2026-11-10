import json
import time
from datetime import datetime

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import ai as ai_plugin
from src.plugins import awareness as aw
from src.plugins import memory as mem
from src.plugins import persona


@pytest.fixture(autouse=True)
def _clean(monkeypatch, store):
    monkeypatch.setattr(mem, "get_store", lambda: store)
    monkeypatch.setattr(persona, "get_store", lambda: store)
    aw.clear_group()
    mem._last_ts.clear()
    mem._daily.clear()

    async def cfg():
        return dict(aw.DEFAULTS)

    monkeypatch.setattr(aw, "config", cfg)
    yield
    aw.clear_group()


def test_clean_facts_filters_sensitive_and_dedupes():
    raw = ["在学 Rust", "在学 Rust", "手机号 13800138000", "常聊 Unity", "密码是 abc", "x",
           "加我 https://a.com", "a" * 60, 123, "- 喜欢猫", "在上海工资很高"]
    assert mem.clean_facts(raw, 8) == ["在学 Rust", "常聊 Unity", "喜欢猫"]
    assert mem.clean_facts(["甲乙", "丙丁", "戊己"], 2) == ["甲乙", "丙丁"]
    assert mem.clean_facts("not a list", 8) == []


def test_familiarity_levels():
    assert [mem.familiarity(n) for n in (0, 2, 3, 14, 15)] == ["刚认识", "刚认识", "认识", "认识", "很熟"]


def test_apply_update_rules():
    old = {1: ["喜欢猫"], 2: []}
    data = {"users": {"1": ["在学 Rust"], "2": ["手机号 13800138000"], "999": ["不在本轮发言人里"], "bad": ["x"]},
            "group": ["常聊 Unity"]}
    users, group = mem.apply_update(old, ["旧群记忆"], data, {1, 2})
    assert users[1] == ["在学 Rust"] and users[2] == [] and 999 not in users
    assert group == ["常聊 Unity"]
    # 清洗后为空 → 不清空已有记忆
    users2, group2 = mem.apply_update({1: ["喜欢猫"]}, ["旧群记忆"], {"users": {"1": ["密码 123"]}, "group": []}, {1})
    assert users2[1] == ["喜欢猫"] and group2 == ["旧群记忆"]


def test_parse_json_object():
    assert mem.parse_json_object('好的 {"users": {}, "group": []} 完毕') == {"users": {}, "group": []}
    assert mem.parse_json_object("没有 json") is None
    assert mem.parse_json_object("{坏的") is None


async def test_store_memory_crud_and_optout(store):
    await store.set_memory_facts("user", 5, 11, ["喜欢猫"])
    await store.set_memory_facts("user", 6, 11, ["在学 Go"])
    await store.set_memory_facts("group", 5, 0, ["常聊 Unity"])
    assert (await store.get_memory("user", 5, 11))["facts"] == ["喜欢猫"]
    assert await store.bump_interactions(5, 11) == 1 and await store.bump_interactions(5, 11) == 2
    assert (await store.get_memory("user", 5, 11))["facts"] == ["喜欢猫"]  # 计数不影响事实
    rows = await store.list_memories(5)
    assert [r["scope"] for r in rows] == ["user", "group"] or {r["scope"] for r in rows} == {"user", "group"}
    assert len(await store.list_user_memories(11)) == 2
    assert await store.delete_user_memories(11) == 2
    assert await store.get_memory("user", 5, 11) is None and await store.get_memory("group", 5) is not None
    await store.set_memory_optout(11, True)
    assert await store.memory_optouts() == {11}
    await store.set_memory_optout(11, False)
    assert await store.memory_optouts() == set()


async def _seed_chat(gid, rows):
    now = time.time()
    for i, (uid, name, text) in enumerate(rows):
        await aw.record(gid, uid, name, 100 + i, Message([MessageSegment.text(text)]), now=now - (len(rows) - i))


def _stub_llm(monkeypatch, reply):
    async def gen(prompt, **kw):
        gen.prompt = prompt
        return reply

    monkeypatch.setattr(ai_plugin, "generate_text", gen)
    return gen


async def test_consolidate_group_end_to_end(store, monkeypatch):
    rows = [(1, "小明", f"我最近在学 Rust 第{i}天") for i in range(22)] + [(2, "小红", f"我在做 Unity 项目{i}") for i in range(12)]
    await _seed_chat(5, rows)
    await store.set_memory_optout(2, True)  # 小红退出：她的发言不应被送去整理
    gen = _stub_llm(monkeypatch, json.dumps({"users": {"1": ["在学 Rust", "手机号 13800138000"], "2": ["做 Unity"]}, "group": ["群里有人学 Rust"]}))
    assert await mem.consolidate_group(5) == "updated"
    assert "小红" not in gen.prompt and "Unity 项目" not in gen.prompt  # 退出者的话没送给模型
    assert "我最近在学 Rust" in gen.prompt
    assert (await store.get_memory("user", 5, 1))["facts"] == ["在学 Rust"]  # 敏感条被过滤
    assert await store.get_memory("user", 5, 2) is None  # 退出者不会被记录
    assert (await store.get_memory("group", 5))["facts"] == ["群里有人学 Rust"]
    # 没有新消息 → 不再重复整理
    assert (await mem.consolidate_group(5)).startswith("skipped")


async def test_consolidate_skips_few_messages_daily_limit_and_bad_output(store, monkeypatch):
    await _seed_chat(5, [(1, "小明", "你好呀")] * 3)
    _stub_llm(monkeypatch, "{}")
    assert await mem.consolidate_group(5) == "skipped:few-messages"
    rows = [(1, "小明", f"消息内容{i}") for i in range(25)]
    await _seed_chat(6, rows)
    _stub_llm(monkeypatch, "不是 json")
    assert await mem.consolidate_group(6) == "skipped:bad-output"
    assert await store.get_memory("user", 6, 1) is None
    mem._last_ts.clear()
    for _ in range(mem.DEFAULTS["memory_daily_limit"]):
        mem._daily[(6, datetime.now().strftime("%Y-%m-%d"))] = mem.DEFAULTS["memory_daily_limit"]
    assert await mem.consolidate_group(6) == "skipped:daily-limit"


async def test_memory_block_content_budget_optout_and_disabled(store):
    await store.set_memory_facts("user", 5, 1, ["在学 Rust", "喜欢猫"])
    for _ in range(3):
        await store.bump_interactions(5, 1)
    await store.set_memory_facts("user", 5, 2, ["做 Unity"])
    await store.set_memory_facts("group", 5, 0, ["群里常聊 Unity"])
    block = await mem.memory_block(5, [1, 2], {1: "小明", 2: "小红"})
    assert "小明（认识）：在学 Rust；喜欢猫" in block and "小红" in block and "本群：群里常聊 Unity" in block
    assert "不要刻意炫耀" in block
    await store.set_memory_optout(2, True)
    assert "小红" not in await mem.memory_block(5, [1, 2], {1: "小明", 2: "小红"})
    assert await mem.memory_block(9, [1], {1: "小明"}) == ""  # 别的群看不到（按群隔离）
    await store.set_kv("memory_enabled", "false")
    assert await mem.memory_block(5, [1], {1: "小明"}) == ""
    await store.set_kv("memory_enabled", "true")
    for i in range(30):
        await store.set_memory_facts("user", 7, 100 + i, [f"爱好很多很多很多很多{i}" * 2])
    big = await mem.memory_block(7, list(range(100, 130)), {})
    assert len(big) <= mem.BLOCK_BUDGET + len(mem.MEMORY_HEADER)


async def test_describe_note_interaction_respect_optout(store):
    await store.set_memory_facts("user", 5, 1, ["喜欢猫"])
    assert "喜欢猫" in await mem.describe_memory(1, 5)
    assert "喜欢猫" in await mem.describe_memory(1, None)  # 私聊展示所有群
    await mem.note_interaction(5, 1)
    assert (await store.get_memory("user", 5, 1))["interactions"] == 1
    await store.set_memory_optout(1, True)
    await mem.note_interaction(5, 1)
    assert (await store.get_memory("user", 5, 1))["interactions"] == 1
    assert "不被记住" in await mem.describe_memory(1, 5)


def test_participants_orders_asker_first():
    now = time.time()
    import asyncio

    async def go():
        for i, (uid, name) in enumerate([(2, "乙"), (3, "丙"), (2, "乙")]):
            await aw.record(5, uid, name, i, Message([MessageSegment.text("嗨")]), now=now - 10 + i)

    asyncio.run(go())
    uids, names = mem.participants(5, 9)
    assert uids[0] == 9 and set(uids) == {9, 2, 3} and names[2] == "乙"


async def test_persona_defaults_sanitize_and_render(store):
    card = await persona.get_persona()
    assert card == persona.DEFAULT_PERSONA
    saved = await persona.save_persona({"name": "星潮酱", "boundaries": "不骂人", "evil": "x", "personality": ""})
    assert saved["name"] == "星潮酱" and "evil" not in saved
    assert persona.HONESTY_LINE in saved["boundaries"] and "不骂人" in saved["boundaries"]  # 诚实条款强制保留
    assert saved["personality"] == persona.DEFAULT_PERSONA["personality"]  # 空值不覆盖
    block = persona.render_persona(saved, "现在是深夜")
    assert "名字：星潮酱" in block and "你当前的状态" in block and "不要主动提起" in block
    await persona.reset_persona()
    assert await persona.get_persona() == persona.DEFAULT_PERSONA


def test_state_line():
    assert "深夜" in persona.state_line(datetime(2026, 11, 10, 3, 0))
    assert persona.state_line(datetime(2026, 11, 10, 16, 0)) == ""
    assert "热闹" in persona.state_line(datetime(2026, 11, 10, 16, 0), recent_messages=20)
