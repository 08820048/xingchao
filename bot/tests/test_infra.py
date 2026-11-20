"""基础设施：数据库版本化迁移、共享 HTTP 客户端、上线播报的离线时长。"""

import sqlite3
import time

from src.store import _MIGRATIONS, Store


def _cols(path, table):
    db = sqlite3.connect(path)
    try:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def _version(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


async def test_fresh_db_runs_all_migrations(tmp_path):
    s = Store(tmp_path / "a.db")
    await s.get_kv("x")
    await s.close()
    assert _version(tmp_path / "a.db") == len(_MIGRATIONS)
    assert {"counted", "message_id", "appeal_reminded"} <= _cols(tmp_path / "a.db", "punishments")


async def test_legacy_db_with_partial_columns_migrates(tmp_path):
    """老库：user_version=0，但旧的手写迁移已经加过部分列。"""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE punishments (id INTEGER PRIMARY KEY, ts TEXT, group_id INTEGER, user_id INTEGER,"
        " word TEXT, action TEXT, mute_minutes INTEGER, reason TEXT, source TEXT,"
        " appeal_status TEXT, appeal_text TEXT, appeal_ts TEXT, counted INTEGER DEFAULT 1)"
    )
    db.commit()
    db.close()
    s = Store(path)
    await s.get_kv("x")
    await s.close()
    assert _version(path) == len(_MIGRATIONS)
    assert {"counted", "message_id", "appeal_note", "resolved_by", "appeal_reminded"} <= _cols(path, "punishments")
    # 再次打开不会重复执行
    s = Store(path)
    await s.get_kv("x")
    await s.close()
    assert _version(path) == len(_MIGRATIONS)


async def test_shared_http_client_reused_and_closed(monkeypatch):
    from src import httpclient

    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)  # 本机代理环境变量不影响用例

    c1 = httpclient.get_http()
    assert httpclient.get_http() is c1
    await httpclient.close_http()
    assert c1.is_closed
    c2 = httpclient.get_http()
    assert c2 is not c1 and not c2.is_closed
    await httpclient.close_http()


async def test_presence_uses_last_seen_from_connect_time(store, monkeypatch):
    """连上后心跳会覆盖 presence_last_seen；离线时长必须用连上瞬间的旧值算。"""
    from src.plugins import presence, updates

    monkeypatch.setattr(presence, "get_store", lambda: store)
    monkeypatch.setattr(presence, "_targets", lambda: [123])
    monkeypatch.setattr(presence.asyncio, "sleep", _no_sleep)

    async def no_pending():
        return None

    monkeypatch.setattr(updates, "pending_entry", no_pending)
    sent = []

    class Bot:
        async def call_api(self, name, **kw):
            sent.append(kw["message"])

    snapshot = str(int(time.time()) - 600)  # 10 分钟前最后在线
    await store.set_kv("presence_last_seen", str(int(time.time())))  # 模拟连上后心跳已覆盖
    await presence._announce_online(Bot(), snapshot)
    assert sent and "离线约 10 分钟" in sent[0]


async def _no_sleep(_s):
    return None
