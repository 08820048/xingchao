import gzip
import sqlite3
from datetime import datetime, timedelta

from src.plugins.backup import latest_backup_age, make_backup, prune_backups, seconds_until_next


def _make_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO kv VALUES ('hello', '世界')")
    db.commit()
    db.close()


def test_make_backup_is_a_valid_restorable_database(tmp_path):
    src = tmp_path / "xingchao.db"
    _make_db(src)
    gz = make_backup(src, tmp_path / "backups", datetime(2026, 11, 6, 3, 30, 0))
    assert gz.name == "xingchao-20261106-033000.db.gz"
    assert not list((tmp_path / "backups").glob("*.db"))  # 临时未压缩文件已清理

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(gz.read_bytes()))
    db = sqlite3.connect(restored)
    assert db.execute("SELECT value FROM kv WHERE key='hello'").fetchone() == ("世界",)
    db.close()


def test_backup_does_not_modify_the_source(tmp_path):
    src = tmp_path / "xingchao.db"
    _make_db(src)
    before = src.read_bytes()
    make_backup(src, tmp_path / "b")
    assert src.read_bytes() == before


def test_prune_keeps_only_the_newest(tmp_path):
    for day in range(1, 11):
        (tmp_path / f"xingchao-202611{day:02d}-033000.db.gz").write_bytes(b"x")
    removed = prune_backups(tmp_path, keep=7)
    assert len(removed) == 3
    left = sorted(p.name for p in tmp_path.glob("*.db.gz"))
    assert len(left) == 7 and left[0] == "xingchao-20261104-033000.db.gz"


def test_prune_never_deletes_everything(tmp_path):
    (tmp_path / "xingchao-20261101-033000.db.gz").write_bytes(b"x")
    assert prune_backups(tmp_path, keep=0) == []  # keep 至少为 1
    assert len(list(tmp_path.glob("*.db.gz"))) == 1


def test_latest_backup_age(tmp_path):
    assert latest_backup_age(tmp_path) is None
    f = tmp_path / "xingchao-20261101-033000.db.gz"
    f.write_bytes(b"x")
    mtime = f.stat().st_mtime
    assert latest_backup_age(tmp_path, now_ts=mtime + 100) == 100


def test_seconds_until_next():
    assert seconds_until_next(datetime(2026, 11, 6, 3, 0, 0)) == 30 * 60  # 当天 03:30
    nxt = seconds_until_next(datetime(2026, 11, 6, 4, 0, 0))
    assert nxt == (23 * 60 + 30) * 60  # 次日 03:30
    assert seconds_until_next(datetime(2026, 11, 6, 3, 30, 0)) == 86400
    assert timedelta(seconds=nxt) < timedelta(days=1)
