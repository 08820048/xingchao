import time

from src.plugins.groupfull import select_candidates

DAY = 86400
NOW = time.time()


def member(uid, role="member", join=90, last=0, level=1):
    return {
        "user_id": uid,
        "role": role,
        "join_time": NOW - join * DAY,
        "last_sent_time": (NOW - last * DAY) if last else 0,
        "level": str(level),
    }


def pick(members, activity=None, **kw):
    kw.setdefault("count", 10)
    kw.setdefault("protect_days", 7)
    kw.setdefault("exempt_ids", set())
    return select_candidates(members, activity or {}, now=NOW, **kw)


def ids(result):
    return [m["user_id"] for m in result]


def test_owner_admin_new_members_and_exempt_are_protected():
    members = [
        member(1, "owner"),
        member(2, "admin"),
        member(3, join=2),  # 新人保护期
        member(4),
        member(5),
    ]
    assert set(ids(pick(members, exempt_ids={5}))) == {4}


def test_never_spoken_comes_before_long_silent_before_recent():
    members = [
        member(10, last=5, level=1),  # 近期发言
        member(11, last=80, level=2),  # 很久没发言
        member(12, last=0, level=1),  # 从未发言
    ]
    assert ids(pick(members)) == [12, 11, 10]


def test_bot_stats_count_as_recent_activity():
    members = [member(20, last=80), member(21, last=80)]
    # 20 在机器人统计里有发言：不应排在前面
    assert ids(pick(members, {20: 5})) == [21, 20]


def test_lower_level_goes_first_among_equally_silent():
    members = [member(30, level=9), member(31, level=1), member(32, level=4)]
    assert ids(pick(members)) == [31, 32, 30]


def test_count_limits_result():
    members = [member(100 + i) for i in range(50)]
    assert len(pick(members, count=7)) == 7


def test_garbage_level_is_treated_as_zero():
    m = member(40)
    m["level"] = "not-a-number"
    assert ids(pick([m, member(41, level=1)])) == [40, 41]
