import json
from datetime import datetime, timedelta

from src.plugins import appeals


class FakeBot:
    self_id = "1"

    def __init__(self):
        self.calls = []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))


def _log(tmp_path, gid, day, rows):
    (tmp_path / f"group-{gid}-{day}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def test_read_original_text(tmp_path):
    _log(tmp_path, 5, "2026-11-10", [
        {"message_id": 11, "raw_plain": "别的消息"},
        {"message_id": 12, "raw_plain": "加我微信 xxx"},
    ])
    assert appeals.read_original_text(tmp_path, 5, 12, "2026-11-10T09:00:00+08:00") == "加我微信 xxx"
    assert appeals.read_original_text(tmp_path, 5, 99, "2026-11-10T09:00:00+08:00") is None
    assert appeals.read_original_text(tmp_path, 5, None, "2026-11-10T09:00:00+08:00") is None
    assert appeals.read_original_text(tmp_path, 6, 12, "2026-11-10T09:00:00+08:00") is None  # 无日志文件


async def _seed(store, **kw):
    pid = await store.add_punishment(
        group_id=5, user_id=42, word="微信", action="recall_mute", mute_minutes=15,
        reason="", message_id=12, **kw,
    )
    await store.appeal_punishment(pid, "我只是在讨论技术")
    return pid


async def test_format_notice_contains_id_original_and_commands(store, tmp_path, monkeypatch):
    monkeypatch.setattr(appeals, "get_store", lambda: store)
    monkeypatch.setattr(appeals, "read_original_text", lambda *a: "加我微信 xxx")
    pid = await _seed(store)
    rec = await store.get_punishment(pid)
    text = await appeals.format_notice(rec, "我只是在讨论技术")
    assert f"#{pid}" in text and "加我微信 xxx" in text and "我只是在讨论技术" in text
    assert f"/申诉通过 {pid}" in text and f"/申诉驳回 {pid}" in text
    assert "已撤回并禁言 15 分钟" in text


async def test_original_block_for_image_sources(store):
    rec = {"source": "vision", "reason": "疑似翻墙广告", "group_id": 5, "ts": "2026-11-10T09:00:00", "message_id": 1}
    assert "图片内容未保存" in await appeals.original_block(rec)
    old = {"source": "sensitive", "group_id": 5, "ts": "2026-11-10T09:00:00", "message_id": None}
    assert "未找到原文" in await appeals.original_block(old)


async def test_resolve_accept_unmutes_notifies_and_syncs_others(store, monkeypatch):
    monkeypatch.setattr(appeals, "get_store", lambda: store)
    monkeypatch.setattr(appeals, "_superuser_ids", lambda: {100, 200})
    sent = []

    async def fake_send(bot, uid, text, gid=None):
        sent.append((uid, text))
        return True

    import src.plugins.sensitive as sens
    monkeypatch.setattr(sens, "send_private", fake_send)
    pid = await _seed(store)
    bot = FakeBot()
    ok, msg = await appeals.resolve_appeal(bot, pid, True, "误判，抱歉", operator="QQ 100", operator_uid=100)
    assert ok and "已解除禁言" in msg
    assert ("set_group_ban", {"group_id": 5, "user_id": 42, "duration": 0}) in bot.calls
    assert sent == [(42, "你关于「微信」的申诉已通过。\n管理员备注：误判，抱歉")]
    # 只通知另一位超管，且不通知处理人自己
    privs = [kw["user_id"] for n, kw in bot.calls if n == "send_private_msg"]
    assert privs == [200]
    rec = await store.get_punishment(pid)
    assert rec["appeal_status"] == "accepted" and rec["resolved_by"] == "QQ 100"
    # 不能重复处理
    ok2, msg2 = await appeals.resolve_appeal(bot, pid, False, operator="QQ 200")
    assert not ok2 and "已处理" in msg2


async def test_resolve_reject_does_not_unmute(store, monkeypatch):
    monkeypatch.setattr(appeals, "get_store", lambda: store)
    monkeypatch.setattr(appeals, "_superuser_ids", lambda: set())
    import src.plugins.sensitive as sens

    async def fake_send(bot, uid, text, gid=None):
        return True

    monkeypatch.setattr(sens, "send_private", fake_send)
    pid = await _seed(store)
    bot = FakeBot()
    ok, _ = await appeals.resolve_appeal(bot, pid, False, "确属广告")
    assert ok and not any(n == "set_group_ban" for n, _ in bot.calls)


async def test_remind_once_after_threshold(store, monkeypatch):
    monkeypatch.setattr(appeals, "get_store", lambda: store)
    monkeypatch.setattr(appeals, "_superuser_ids", lambda: {100})
    pid = await _seed(store)
    bot = FakeBot()
    now = datetime.now().astimezone()
    assert await appeals.remind_tick(bot, now) == 0  # 刚提交，不催
    later = now + timedelta(hours=appeals.REMIND_HOURS + 1)
    assert await appeals.remind_tick(bot, later) == 1
    assert f"#{pid}" in bot.calls[0][1]["message"]
    assert await appeals.remind_tick(bot, later) == 0  # 只催一次


async def test_store_migration_adds_columns(tmp_path):
    import aiosqlite

    from src.store import Store

    db = tmp_path / "old.db"
    async with aiosqlite.connect(db) as c:  # 模拟旧库：punishments 缺新列
        await c.execute(
            "CREATE TABLE punishments (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
            " group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, word TEXT NOT NULL,"
            " action TEXT NOT NULL, mute_minutes INTEGER DEFAULT 0, reason TEXT DEFAULT '',"
            " source TEXT DEFAULT 'sensitive', appeal_status TEXT DEFAULT 'none',"
            " appeal_text TEXT DEFAULT '', appeal_ts TEXT)"
        )
        await c.execute("INSERT INTO punishments (ts, group_id, user_id, word, action) VALUES ('2026-11-01T00:00:00', 1, 2, 'w', 'recall')")
        await c.commit()
    store = Store(db)
    rec = await store.get_punishment(1)
    assert rec["message_id"] is None and rec["appeal_note"] == ""
    await store.close()
