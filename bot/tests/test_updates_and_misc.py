import json

from src.plugins import updates
from src.plugins.presence import format_duration
from src.plugins.voice import pick_character


def test_format_entry_is_well_formed():
    text = updates.format_entry(
        {"version": "1.2.3", "date": "2026-11-06", "added": ["新功能 A"], "improved": [], "fixed": ["修复 B"]}
    )
    assert text.startswith("🚀 星潮 v1.2.3 更新")
    assert "✨ 新增" in text and "• 新功能 A" in text
    assert "🐛 修复" in text and "• 修复 B" in text
    assert "🔧 优化" not in text  # 空分区不出现


def test_shipped_changelog_is_valid_and_newest_first():
    entries = updates.load_changelog()
    assert entries, "changelog.json 不能为空"
    for e in entries:
        assert e["version"] and e["date"]
        assert any(e.get(k) for k in ("added", "improved", "fixed"))
    versions = [tuple(int(x) for x in e["version"].split(".")) for e in entries]
    assert versions == sorted(versions, reverse=True), "最新版本必须在最前"


async def test_pending_entry_logic(store, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "get_store", lambda: store)
    path = tmp_path / "changelog.json"
    path.write_text(json.dumps([{"version": "9.9.9", "date": "2026-01-01", "added": ["x"]}]), encoding="utf-8")
    monkeypatch.setattr(updates, "CHANGELOG_PATH", path)

    assert (await updates.pending_entry())["version"] == "9.9.9"
    await store.set_kv("update_announced_version", "9.9.9")
    assert await updates.pending_entry() is None  # 已公告过
    await store.set_kv("update_announced_version", "9.9.8")
    assert await updates.pending_entry() is not None
    await store.set_kv("update_notify", "false")
    assert await updates.pending_entry() is None  # 开关关闭


async def test_notify_false_entry_is_not_pending(store, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "get_store", lambda: store)
    path = tmp_path / "c.json"
    path.write_text(json.dumps([{"version": "1.0.0", "date": "d", "added": ["x"], "notify": False}]), encoding="utf-8")
    monkeypatch.setattr(updates, "CHANGELOG_PATH", path)
    assert await updates.pending_entry() is None


def test_format_duration():
    assert format_duration(30) == "30 秒"
    assert format_duration(300) == "5 分钟"
    assert format_duration(7200) == "2 小时"
    assert format_duration(90000) == "1 天 1 小时"


def test_pick_character():
    chars = [("a1", "温柔姐姐"), ("a2", "元气少女"), ("a3", "温柔大叔")]
    assert pick_character("2", chars) == ("a2", "元气少女")  # 序号
    assert pick_character("a3", chars) == ("a3", "温柔大叔")  # ID
    assert pick_character("元气少女", chars) == ("a2", "元气少女")  # 精确名称
    assert pick_character("少女", chars) == ("a2", "元气少女")  # 唯一包含
    assert pick_character("温柔", chars) is None  # 有歧义不猜
    assert pick_character("9", chars) is None
