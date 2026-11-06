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
