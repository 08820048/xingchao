from datetime import datetime
from pathlib import Path

from src.plugins import feeds

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_zaobao_extracts_items():
    parsed = feeds.parse_zaobao(_read("zaobao_2026-11-10.html"))
    assert parsed and (parsed["month"], parsed["day"]) == (11, 10)
    assert len(parsed["items"]) == 7
    first = parsed["items"][0]
    assert "Anthropic" in first["title"] and first["body"]
    assert first["url"].startswith("https://") and first["source"] == "TechCrunch"
    assert '"' in first["body"] or "&quot;" not in first["body"]  # HTML 实体已解码


def test_parse_zaobao_rejects_fallback_index_page():
    # 站点对未发布日期会回退到首页(200)，h1 不是"X月X日 AI 早报"，必须当作未发布
    assert feeds.parse_zaobao(_read("zaobao_not_ready.html")) is None


def test_format_zaobao_head_and_nodes():
    parsed = feeds.parse_zaobao(_read("zaobao_2026-11-10.html"))
    c = feeds.format_zaobao(parsed, "https://x/2026-11-10.html")
    assert c.head.startswith("📰 11月10日 AI 早报（共 7 条）")
    assert len(c.items) == 7 and c.items[0].startswith("1. ")
    c2 = feeds.format_zaobao(parsed, "u", bodies=["要点"] * 7)
    assert "要点" in c2.items[0] and parsed["items"][0]["body"] not in c2.items[0]


def test_parse_numbered_lines():
    assert feeds.parse_numbered_lines("1. 甲\n2. 乙", 2) == ["甲", "乙"]
    assert feeds.parse_numbered_lines("1. 甲", 2) is None  # 条数不符 → 回退原文
    assert feeds.parse_numbered_lines("1. 甲\n3. 丙", 2) is None


def test_slot_state():
    sub = {"time": "08:00", "repeat": "daily", "weekday": None}
    at = lambda h, m: datetime(2026, 11, 10, h, m)  # noqa: E731  (周二)
    assert feeds.slot_state(sub, at(7, 59)) == "before"
    assert feeds.slot_state(sub, at(8, 0)) == "due"
    assert feeds.slot_state(sub, at(10, 0)) == "due"
    assert feeds.slot_state(sub, at(10, 1)) == "expired"
    weekend = {**sub, "repeat": "weekend"}
    assert feeds.slot_state(weekend, at(8, 0)) == "skip"


def test_chunk_respects_limit():
    chunks = feeds._chunk(["a" * 800, "b" * 800, "c" * 100], limit=1500)
    assert len(chunks) == 2 and all(len(c) <= 1700 for c in chunks)


class FakeBot:
    self_id = "1"

    def __init__(self, fail_forward=False):
        self.calls, self.fail_forward = [], fail_forward

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if name == "send_group_forward_msg" and self.fail_forward:
            raise RuntimeError("boom")


async def test_send_content_forward_and_fallback():
    c = feeds.FeedContent(head="H", items=["one", "two"])
    bot = FakeBot()
    await feeds.send_content(bot, 5, c)
    assert [n for n, _ in bot.calls] == ["send_group_msg", "send_group_forward_msg"]
    bot = FakeBot(fail_forward=True)
    await feeds.send_content(bot, 5, c)
    assert [n for n, _ in bot.calls] == ["send_group_msg", "send_group_forward_msg", "send_group_msg"]


async def test_run_sub_not_ready_does_not_send(monkeypatch):
    async def not_ready(params, ai, now):
        return None

    monkeypatch.setitem(feeds.SOURCES, "zaobao", ("MuseAI 早报", not_ready))
    feeds._cache.clear()
    bot = FakeBot()
    sub = {"id": 1, "group_id": 5, "source": "zaobao", "params": "{}", "ai_summary": False}
    ok, msg = await feeds.run_sub(bot, sub, datetime(2026, 11, 10, 8, 0))
    assert not ok and "尚未发布" in msg and bot.calls == []


async def test_feed_sub_crud(store):
    sid = await store.add_feed_sub(5, "github", "09:00", "daily", None, '{"since":"daily"}', False)
    await store.update_feed_sub(sid, enabled=False, ai_summary=True)
    (s,) = await store.list_feed_subs()
    assert s["source"] == "github" and not s["enabled"] and s["ai_summary"]
    assert await store.list_feed_subs(enabled_only=True) == []
    assert await store.delete_feed_sub(sid)


def test_github_fixture_parses_and_formats():
    from src.plugins.github import _parse_trending, format_trending

    items = _parse_trending(_read("github_trending.html"))
    assert len(items) == 4 and all(it["repo"] and "/" in it["repo"] for it in items)
    assert format_trending(items, "daily", "").startswith("📈 GitHub 今日趋势")
