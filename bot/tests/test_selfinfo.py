import pytest

from src.plugins import selfinfo


def test_parse_and_text():
    info = selfinfo.parse_info({"user_id": 1, "nickname": "n", "is_vip": True, "is_years_vip": True,
                                "vip_level": 5, "qqLevel": 30, "reg_time": 1400000000}, now=1)
    assert selfinfo.vip_text(info) == "SVIP（年费），VIP5"
    assert "QQ 等级 30" in selfinfo.summary(info) and "2014 年注册" in selfinfo.summary(info)


def test_missing_fields_are_unknown_not_false():
    info = selfinfo.parse_info({"user_id": 1})
    assert info["svip"] is None
    assert selfinfo.vip_text(info) == "会员状态未知"
    assert selfinfo.vip_text(selfinfo.parse_info({"is_vip": False, "is_years_vip": False})) == "不是会员"


def test_summary_empty_without_data():
    assert selfinfo.summary({}) == ""


class _Bot:
    self_id = "7"

    def __init__(self, raw):
        self.raw = raw

    async def call_api(self, api, **kw):
        assert api == "get_stranger_info" and kw["user_id"] == 7
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


@pytest.mark.asyncio
async def test_refresh_keeps_old_on_failure(monkeypatch, tmp_path):
    from src.store import get_store  # noqa: F401

    saved = {}

    class S:
        async def set_kv(self, k, v):
            saved[k] = v

    monkeypatch.setattr(selfinfo, "get_store", lambda: S())
    monkeypatch.setattr(selfinfo, "_info", {})
    got = await selfinfo.refresh(_Bot({"user_id": 7, "is_vip": True, "is_years_vip": False}))
    assert got["svip"] is True and selfinfo.KV_KEY in saved
    again = await selfinfo.refresh(_Bot(RuntimeError("x")))
    assert again["svip"] is True
    assert "SVIP" in selfinfo.describe()
