import time

import pytest

from src.plugins import invites as inv


@pytest.fixture(autouse=True)
def _clean():
    inv._PENDING.clear()
    inv._SEQ[0] = 0
    yield
    inv._PENDING.clear()


class FakeBot:
    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if self.exc:
            raise self.exc
        return {}


def test_add_describe_list_and_expire():
    now = time.time()
    s1 = inv.add_pending("friend", 11, "f1", "我是群友", now=now)
    s2 = inv.add_pending("group_invite", 12, "g1", "", 999, now=now)
    assert (s1, s2) == (1, 2)
    t1 = inv.describe(s1, inv._PENDING[s1], "小明")
    assert "好友申请 #1" in t1 and "小明" in t1 and "/同意邀请 1" in t1 and "我是群友" in t1
    assert "拉群邀请 #2" in inv.describe(s2, inv._PENDING[s2]) and "目标群：999" in inv.describe(s2, inv._PENDING[s2])
    assert "#1 好友申请" in inv.list_text() and "#2 拉群邀请" in inv.list_text()
    inv.add_pending("friend", 13, "f3", "", now=now + inv.EXPIRE_SECONDS + 10)  # 新增时清理过期项
    assert set(inv._PENDING) == {3}
    inv._PENDING.clear()
    assert "没有待处理" in inv.list_text()


async def test_resolve_friend_and_group_invite():
    s1 = inv.add_pending("friend", 11, "f1", "hi")
    s2 = inv.add_pending("group_invite", 12, "g1", "", 999)
    bot = FakeBot()
    ok, msg = await inv.resolve(bot, s1, True)
    assert ok and bot.calls[-1] == ("set_friend_add_request", {"flag": "f1", "approve": True, "remark": ""})
    ok, msg = await inv.resolve(bot, s2, True)
    assert ok and bot.calls[-1][0] == "set_group_add_request" and bot.calls[-1][1]["sub_type"] == "invite"
    assert "/group add 999" in msg  # 通过后提示加白名单
    assert inv._PENDING == {}


async def test_resolve_reject_unknown_and_failure():
    s = inv.add_pending("group_invite", 12, "g1", "", 999)
    bot = FakeBot()
    ok, msg = await inv.resolve(bot, s, False, "不需要")
    assert ok and bot.calls[-1][1]["approve"] is False and bot.calls[-1][1]["reason"] == "不需要"
    assert "/group add" not in msg
    ok, msg = await inv.resolve(bot, 99, True)
    assert not ok and "没有找到" in msg
    s = inv.add_pending("friend", 11, "f1", "")
    ok, msg = await inv.resolve(FakeBot(exc=RuntimeError("flag expired")), s, True)
    assert not ok and "过期" in msg and s not in inv._PENDING
