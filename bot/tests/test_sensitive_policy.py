"""升级处罚与踢人防线：这些规则错一次就是误踢真人，必须有测试。"""

import pytest

from src.plugins.sensitive import (
    escalation_plan,
    kick_decision,
    parse_ids,
    trial_remaining,
)

DAY = 86400


@pytest.mark.parametrize(
    ("strike", "base", "expected"),
    [
        (1, 0, ("base", 0)),
        (1, 5, ("base", 5)),
        (2, 0, ("mute", 20)),  # 未配置基础禁言：按 10 分钟翻倍
        (2, 30, ("mute", 60)),
        (3, 5, ("mute", 30 * 24 * 60)),  # 最长 30 天
        (4, 5, ("kick", 0)),
        (9, 5, ("kick", 0)),
    ],
)
def test_escalation_plan(strike, base, expected):
    assert escalation_plan(strike, base) == expected


def test_escalation_mute_never_exceeds_platform_limit():
    kind, minutes = escalation_plan(2, 10**9)
    assert kind == "mute" and minutes == 30 * 24 * 60


def test_trial_remaining():
    assert trial_remaining(0, 0, 7) == 7
    assert trial_remaining(0, 3.2 * DAY, 7) == 4  # 向上取整
    assert trial_remaining(0, 7 * DAY, 7) == 0
    assert trial_remaining(0, 100 * DAY, 7) == 0
    assert trial_remaining(0, 0, 0) == 0  # 无试运行


def test_parse_ids():
    assert parse_ids("10001, 10002，abc  10003、10004;x") == {10001, 10002, 10003, 10004}
    assert parse_ids("") == set()
    assert parse_ids(None) == set()  # type: ignore[arg-type]


def _decide(**over):
    base = dict(
        role="member",
        user_id=555,
        superusers={1},
        exempt_ids=set(),
        kicks_today=0,
        daily_cap=3,
        trial_left_days=0,
    )
    base.update(over)
    return kick_decision(**base)


def test_kick_allowed_in_the_normal_case():
    assert _decide() == (True, "")


@pytest.mark.parametrize(
    ("over", "keyword"),
    [
        ({"user_id": 1}, "超管"),
        ({"role": "admin"}, "管理员"),
        ({"role": "owner"}, "管理员"),
        ({"exempt_ids": {555}}, "豁免"),
        ({"trial_left_days": 3}, "试运行"),
        ({"kicks_today": 3}, "上限"),
        ({"kicks_today": 99}, "上限"),
    ],
)
def test_kick_blocked(over, keyword):
    allowed, why = _decide(**over)
    assert allowed is False
    assert keyword in why


def test_daily_cap_zero_means_unlimited():
    assert _decide(kicks_today=1000, daily_cap=0) == (True, "")


def test_blocking_order_prefers_hard_exemptions():
    # 超管同时处在试运行期：原因应是更硬的「超管」
    allowed, why = _decide(user_id=1, trial_left_days=5)
    assert not allowed and "超管" in why


# ---------------------------------------------------------------- 群内提示里的违规次数

def test_strike_note_shows_first_offense():
    """第 1 次也要显示——否则会被当成"次数统计没生效"。"""
    from src.plugins.sensitive import strike_note

    assert strike_note(1, 30, "violation", True) == "（30 天内第 1 次违规）"


def test_strike_note_shows_later_offenses():
    from src.plugins.sensitive import strike_note

    assert strike_note(2, 30, "violation", True) == "（30 天内第 2 次违规）"
    assert strike_note(4, 7, "violation", True) == "（7 天内第 4 次违规）"


def test_strike_note_explains_fallback_not_counted():
    """兜底撤回确实处罚了但没计次，必须说明，否则看起来像功能坏了。"""
    from src.plugins.sensitive import strike_note

    note = strike_note(0, 30, "unknown", True)
    assert "未计入" in note and "AI 复核不可用" in note


def test_strike_note_silent_when_escalation_disabled():
    from src.plugins.sensitive import strike_note

    assert strike_note(0, 30, "violation", False) == ""


def test_punishment_notice_mute_includes_count_and_minutes():
    from src.plugins.sensitive import punishment_notice

    text = punishment_notice(
        10001, strike=2, days=30, decision="violation", escalation_enabled=True,
        kicked=False, muted=True, mute_minutes=20, recalled=True,
    )
    assert "禁言 20 分钟" in text and "第 2 次违规" in text


def test_punishment_notice_recall_only_also_shows_count():
    """只撤回不禁言时，以前完全不显示次数。"""
    from src.plugins.sensitive import punishment_notice

    text = punishment_notice(
        10001, strike=1, days=30, decision="violation", escalation_enabled=True,
        kicked=False, muted=False, mute_minutes=0, recalled=True,
    )
    assert "消息已被撤回" in text and "第 1 次违规" in text


def test_punishment_notice_kick_wording():
    from src.plugins.sensitive import punishment_notice

    text = punishment_notice(
        10001, strike=4, days=30, decision="violation", escalation_enabled=True,
        kicked=True, muted=False, mute_minutes=0, recalled=True,
    )
    assert "移出群聊" in text and "第 4 次违规" in text


def test_punishment_notice_fallback_path():
    from src.plugins.sensitive import punishment_notice

    text = punishment_notice(
        10001, strike=0, days=30, decision="unknown", escalation_enabled=True,
        kicked=False, muted=True, mute_minutes=15, recalled=True,
    )
    assert "未计入违规次数" in text


async def test_count_strikes_matches_flow(store):
    """完整链路：连续违规次数递增，兜底与申诉通过不计入。"""
    from src.plugins.sensitive import escalation_plan

    for i in range(1, 4):
        prior = await store.count_strikes(5, 10001, days=30)
        assert prior == i - 1
        kind, minutes = escalation_plan(prior + 1, 10)
        await store.add_punishment(5, 10001, "词", kind, minutes, "", "sensitive", counted=True)

    assert await store.count_strikes(5, 10001, days=30) == 3
    await store.add_punishment(5, 10001, "词", "recall", 0, "", "sensitive", counted=False)
    assert await store.count_strikes(5, 10001, days=30) == 3      # 兜底不计
    rows = await store.list_punishments(group_id=5, limit=10)
    counted_ids = [r["id"] for r in rows if r["counted"]]
    assert len(counted_ids) == 3
    # 必须按真实顺序来：先由当事人申诉（置 pending），管理员才能处理
    assert await store.resolve_appeal(counted_ids[0], True, "误判", "admin") is None
    assert await store.appeal_punishment(counted_ids[0], "我只是在讨论技术") is True
    assert await store.resolve_appeal(counted_ids[0], True, "误判", "admin") is not None
    assert await store.count_strikes(5, 10001, days=30) == 2      # 申诉通过不再计
    # 未计入的记录（兜底撤回）申诉通过不会让次数变成负数
    uncounted = [r["id"] for r in rows if not r["counted"]]
    assert uncounted, "兜底撤回的记录应标记 counted=False"
    assert await store.count_strikes(5, 10001, days=30) == 2
