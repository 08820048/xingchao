"""工作台账：动作计数、按来源汇总、空状态、时间区间。"""

from datetime import datetime, timedelta

import pytest

from src.plugins import worklog


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    monkeypatch.setattr(worklog, "get_store", lambda: store)
    yield


def _day(offset: int = 0) -> str:
    return (datetime.now().astimezone().date() - timedelta(days=offset)).isoformat()


async def _punish(store, day: str, source: str, mute: int = 0):
    """直接写一条处罚记录（绕开 ts=now 的默认值，便于构造区间）。"""
    conn = await store._ensure()
    await conn.execute(
        "INSERT INTO punishments (ts, group_id, user_id, word, action, mute_minutes,"
        " reason, source, counted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (f"{day}T10:00:00+08:00", 555, 10001, "w", "recall", mute, "", source),
    )
    await conn.commit()


# ---------------------------------------------------------------- 计数

async def test_action_counters_accumulate(store):
    await store.incr_action(555, "ai_reply")
    await store.incr_action(555, "ai_reply")
    await store.incr_action(555, "kb_answer")
    totals = await store.action_totals(_day(), _day())
    assert totals == {"ai_reply": 2, "kb_answer": 1}


async def test_action_totals_are_range_and_group_scoped(store):
    await store.incr_action(555, "ai_reply", day=_day(0))
    await store.incr_action(555, "ai_reply", day=_day(10))
    await store.incr_action(666, "ai_reply", day=_day(0))
    week = await store.action_totals(_day(6), _day(0))
    assert week == {"ai_reply": 2}                      # 两个群、一周内
    assert await store.action_totals(_day(6), _day(0), 555) == {"ai_reply": 1}
    # 10 天前的落在窗口外
    assert await store.action_totals(_day(6), _day(0)) == week


async def test_action_by_group(store):
    await store.incr_action(555, "kb_answer")
    await store.incr_action(666, "kb_answer")
    await store.incr_action(666, "recall_query")
    by_group = await store.action_by_group(_day(), _day())
    assert by_group[555] == {"kb_answer": 1}
    assert by_group[666] == {"kb_answer": 1, "recall_query": 1}


# ---------------------------------------------------------------- 台账文本

async def test_worklog_counts_moderation_by_source(store):
    await _punish(store, _day(1), "sensitive", mute=15)
    await _punish(store, _day(2), "vision")
    await _punish(store, _day(3), "ad")
    await _punish(store, _day(4), "qrcode")
    text = await worklog.build_worklog(555, days=7)
    assert "拦下违规言论 1 次" in text
    assert "拦下违规图片 1 次" in text
    assert "拦下群内广告 1 次" in text
    assert "拦下引流二维码 1 次" in text
    assert "其中禁言 1 人次" in text


async def test_worklog_lists_help_actions(store):
    await store.incr_action(555, "ai_reply")
    await store.incr_action(555, "ai_reply")
    await store.incr_action(555, "kb_answer")
    text = await worklog.build_worklog(555, days=7)
    assert "回答群友提问 2 次" in text
    assert "命中群知识" in text
    assert "替人打了 1 遍同样的答案" in text


async def test_worklog_shows_week_over_week(store):
    await store.incr_action(555, "ai_reply", day=_day(1))
    await store.incr_action(555, "ai_reply", day=_day(2))
    await store.incr_action(555, "ai_reply", day=_day(9))
    text = await worklog.build_worklog(555, days=7)
    assert "较上期" in text


async def test_worklog_empty_state_is_honest(store):
    text = await worklog.build_worklog(555, days=7)
    assert "没派上什么用场" in text
    assert "拦下" not in text
    assert "次" not in text       # 不编造任何数字行
    assert "·" not in text


async def test_worklog_excludes_old_records(store):
    await _punish(store, _day(30), "sensitive")
    text = await worklog.build_worklog(555, days=7)
    assert "拦下" not in text


async def test_worklog_scope_label(store):
    assert "群 555" in await worklog.build_worklog(555, days=7)
    assert "全部群" in await worklog.build_worklog(None, days=7)


async def test_worklog_aggregates_all_groups(store):
    await _punish(store, _day(1), "sensitive")
    await store.incr_action(555, "ai_reply")
    await store.incr_action(666, "ai_reply")
    text = await worklog.build_worklog(None, days=7)
    assert "回答群友提问 2 次" in text


async def test_unknown_action_still_reported(store):
    await store.incr_action(555, "some_new_thing")
    text = await worklog.build_worklog(555, days=7)
    assert "some_new_thing 1 次" in text


async def test_worklog_disabled_check(store):
    await store.set_kv("worklog_enabled", "false")
    assert await worklog.is_enabled() is False
    await store.set_kv("worklog_enabled", "true")
    assert await worklog.is_enabled() is True


async def test_worklog_has_no_trailing_blank_lines(store):
    await store.incr_action(555, "kb_answer")
    text = await worklog.build_worklog(555, days=7)
    assert not text.endswith("\n")
    assert "\n\n\n" not in text
