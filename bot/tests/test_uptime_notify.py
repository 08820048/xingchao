"""连接记录与超管通知过滤。"""

from __future__ import annotations

from src.notify import notify_targets
from src.store import Store


async def test_bot_events_roundtrip(store: Store):
    await store.add_bot_event("online", "QQ 1 已连接")
    await store.add_bot_event("offline", "下线通知：登录已失效", duration_seconds=7200)
    events = await store.list_bot_events(limit=10)
    assert [e["event"] for e in events] == ["offline", "online"]
    assert events[0]["duration_seconds"] == 7200
    assert events[0]["detail"].startswith("下线通知")
    assert (await store.last_bot_event("online"))["event"] == "online"
    assert (await store.last_bot_event("offline"))["duration_seconds"] == 7200
    assert await store.last_bot_event("nope") is None


class FriendBot:
    def __init__(self, self_id: str, friends: list[int], fail: set[int] | None = None):
        self.self_id = self_id
        self.friends = friends
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if name == "get_friend_list":
            return [{"user_id": uid} for uid in self.friends]
        if name == "send_private_msg":
            if kw["user_id"] in self.fail:
                raise RuntimeError("发送失败，请先添加对方为好友")
            return {"message_id": 1}
        raise AssertionError(name)


async def test_notify_only_friends(store, monkeypatch):
    import src.notify as notify

    monkeypatch.setattr(notify, "get_store", lambda: store)
    bot = FriendBot("1000", friends=[101])
    await notify_targets(bot, {101, 102}, "告警")
    sends = [kw["user_id"] for n, kw in bot.calls if n == "send_private_msg"]
    assert sends == [101]


async def test_notify_allowlist_bypasses_friend_filter(store, monkeypatch):
    import src.notify as notify

    monkeypatch.setattr(notify, "get_store", lambda: store)
    await store.set_kv("notify_superusers_allowlist", "201, 202")
    bot = FriendBot("1001", friends=[201])
    await notify_targets(bot, {999}, "告警")
    sends = [kw["user_id"] for n, kw in bot.calls if n == "send_private_msg"]
    assert sends == [201, 202]
    assert not any(n == "get_friend_list" for n, _ in bot.calls)


async def test_notify_failed_target_is_skipped_for_a_while(store, monkeypatch):
    import src.notify as notify

    monkeypatch.setattr(notify, "get_store", lambda: store)
    bot = FriendBot("1002", friends=[301], fail={301})
    await notify_targets(bot, {301}, "第一次")
    await notify_targets(bot, {301}, "第二次")
    assert [n for n, _ in bot.calls].count("send_private_msg") == 1
