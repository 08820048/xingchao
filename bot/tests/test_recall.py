"""群聊回忆：增量读取 jsonl、检索打分、时间窗口、指令渲染。"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins import recall


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    # recall.py 在模块顶部绑定了 get_store，patch 模块自身的名字才有效
    monkeypatch.setattr(recall, "get_store", lambda: store)
    recall._reset_state()

    async def enabled():
        return True

    monkeypatch.setattr(recall, "is_enabled", enabled)
    yield
    recall._reset_state()


def _write(path: Path, rows, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _rec(gid=555, uid=10001, mid=1, text="你好", hours_ago=0.0):
    ts = datetime.now().astimezone() - timedelta(hours=hours_ago)
    return {
        "time": ts.isoformat(timespec="seconds"),
        "group_id": gid,
        "user_id": uid,
        "message_id": mid,
        "raw_plain": text,
    }


@pytest.fixture
def logdir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    return d


def _patch_dir(monkeypatch, d):
    monkeypatch.setattr(
        recall, "get_config", lambda: SimpleNamespace(xingchao_log_dir=d)
    )


# ---------------------------------------------------------------- 读取

def test_ingest_skips_empty_text_and_bad_json(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps(_rec(mid=1, text="正常消息"), ensure_ascii=False) + "\n")
        f.write(json.dumps(_rec(mid=2, text=""), ensure_ascii=False) + "\n")     # 纯图/表情
        f.write("{坏掉的 json\n")
        f.write(json.dumps(_rec(mid=3, text="第二条"), ensure_ascii=False) + "\n")
    assert recall.ingest_file(p, 1000) == 2
    assert [d.message_id for d in recall._docs[555]] == [1, 3]


def test_ingest_is_incremental(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    _write(p, [_rec(mid=1, text="第一条")])
    assert recall.ingest_file(p, 1000) == 1
    assert recall.ingest_file(p, 1000) == 0          # 没有新增
    _write(p, [_rec(mid=2, text="第二条")], append=True)
    assert recall.ingest_file(p, 1000) == 1          # 只读新增部分
    assert len(recall._docs[555]) == 2


def test_partial_line_is_not_consumed(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    _write(p, [_rec(mid=1, text="完整")])
    assert recall.ingest_file(p, 1000) == 1          # 先消化掉完整的那条
    with p.open("a", encoding="utf-8") as f:
        f.write('{"time": "2026-11-15T09:00:00+08:00", "group_id": 555')  # 写了一半
    assert recall.ingest_file(p, 1000) == 0
    offset_before = recall._loaded[p]
    with p.open("a", encoding="utf-8") as f:
        f.write(', "user_id": 10001, "message_id": 2, "raw_plain": "补完了"}\n')
    assert recall.ingest_file(p, 1000) == 1          # 补齐后能被读到
    assert recall._loaded[p] > offset_before


def test_truncated_file_is_reread_from_start(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    _write(p, [_rec(mid=1, text="旧内容很长很长")])
    recall.ingest_file(p, 1000)
    _write(p, [_rec(mid=9, text="重建后")])           # 覆盖写，文件变小
    assert recall.ingest_file(p, 1000) == 1
    assert recall._docs[555][-1].message_id == 9


def test_max_docs_keeps_most_recent(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    _write(p, [_rec(mid=i, text=f"消息{i}") for i in range(10)])
    recall.ingest_file(p, 3)
    assert [d.message_id for d in recall._docs[555]] == [7, 8, 9]


def test_refresh_only_reads_whitelisted_groups(monkeypatch, logdir):
    _patch_dir(monkeypatch, logdir)
    _write(logdir / "group-111-2026-11-15.jsonl", [_rec(gid=111, mid=1, text="甲群消息")])
    _write(logdir / "group-222-2026-11-15.jsonl", [_rec(gid=222, mid=1, text="乙群消息")])
    total = 0
    for path in recall._log_paths(logdir, {111}):
        total += recall.ingest_file(path, 1000)
    assert total == 1 and 111 in recall._docs and 222 not in recall._docs


# ---------------------------------------------------------------- 检索

def test_search_finds_relevant_and_ranks_specific_higher(tmp_path, logdir):
    _write(logdir / "group-555-2026-11-15.jsonl", [
        _rec(mid=1, text="今天吃了个苹果", hours_ago=5),
        _rec(mid=2, text="那个 Rust 教程的链接我发一下", hours_ago=4),
        _rec(mid=3, text="Rust 学习资料整理好了", hours_ago=3),
        _rec(mid=4, text="晚上有人打游戏吗", hours_ago=2),
    ])
    for path in logdir.glob("*.jsonl"):
        recall.ingest_file(path, 1000)
    hits = recall.search_sync(555, "Rust 教程", days=90, limit=5)
    assert hits, "应该能搜到 Rust 相关消息"
    assert hits[0][1].message_id == 2      # 同时命中 Rust 与 教程
    assert all("Rust" in d.text or "教程" in d.text for _s, d in hits)


def test_search_respects_time_window(logdir):
    _write(logdir / "group-555-2026-11-15.jsonl", [
        _rec(mid=1, text="很久以前聊过那个表格", hours_ago=24 * 40),
        _rec(mid=2, text="昨天又提到那个表格", hours_ago=24),
    ])
    for path in logdir.glob("*.jsonl"):
        recall.ingest_file(path, 1000)
    recent = recall.search_sync(555, "表格", days=7)
    assert [d.message_id for _s, d in recent] == [2]
    both = recall.search_sync(555, "表格", days=90)
    assert {d.message_id for _s, d in both} == {1, 2}


def test_search_ignores_stopword_only_query(logdir):
    _write(logdir / "group-555-2026-11-15.jsonl", [_rec(text="怎么怎么样")])
    for path in logdir.glob("*.jsonl"):
        recall.ingest_file(path, 1000)
    assert recall.search_sync(555, "怎么", days=90) == []
    assert recall.search_sync(555, "   ", days=90) == []


def test_search_is_scoped_per_group(logdir):
    _write(logdir / "group-555-2026-11-15.jsonl", [_rec(gid=555, mid=1, text="甲群的暗号")])
    _write(logdir / "group-666-2026-11-15.jsonl", [_rec(gid=666, mid=2, text="乙群的暗号")])
    for path in logdir.glob("*.jsonl"):
        recall.ingest_file(path, 1000)
    hits = recall.search_sync(555, "暗号", days=90)
    assert [d.message_id for _s, d in hits] == [1]


def test_format_hits_shows_time_name_and_truncates():
    doc = recall.Doc(group_id=1, message_id=1, ts=datetime(2026, 11, 15, 9, 30),
                     user_id=42, text="很长的一段话" * 60)
    out = recall.format_hits([(2.0, doc)], {42: "小明"})
    assert out.startswith("[11-15 09:30] 小明：")
    assert out.endswith("…") and len(out) < 260


async def test_recall_text_reports_empty_result(monkeypatch, logdir):
    _patch_dir(monkeypatch, logdir)
    text = await recall.recall_text(None, 555, "不存在的东西")
    assert "没找到" in text


async def test_recall_text_lists_hits(monkeypatch, logdir):
    _patch_dir(monkeypatch, logdir)
    _write(logdir / "group-555-2026-11-15.jsonl", [_rec(mid=1, text="团购的表格在这里")])
    text = await recall.recall_text(None, 555, "表格")
    assert "找到 1 条" in text and "团购的表格" in text


async def test_disabled_feature_is_inert(monkeypatch, store, logdir):
    _patch_dir(monkeypatch, logdir)
    _write(logdir / "group-555-2026-11-15.jsonl", [_rec(text="随便说说")])

    async def off():
        return False

    monkeypatch.setattr(recall, "is_enabled", off)
    assert await recall.search(555, "随便") == []
    assert await recall.refresh() == 0


def test_max_docs_change_applies_without_restart(logdir):
    p = logdir / "group-555-2026-11-15.jsonl"
    _write(p, [_rec(mid=i, text=f"消息{i}") for i in range(10)])
    recall.ingest_file(p, 10)
    assert len(recall._docs[555]) == 10
    _write(p, [_rec(mid=99, text="再来一条")], append=True)
    recall.ingest_file(p, 4)      # 面板调小上限后立刻收敛
    assert len(recall._docs[555]) == 4
    assert recall._docs[555][-1].message_id == 99
