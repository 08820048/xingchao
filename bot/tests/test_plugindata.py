import pytest

from src.aitools import build_tools, plugindata


def test_self_info_tool_registered():
    assert "get_self_info" in {t[0]["function"]["name"] for t in build_tools()}


class _Bot:
    self_id = "100"


@pytest.mark.asyncio
async def test_self_info_tool_returns_text(monkeypatch):
    from src.plugins import selfinfo

    monkeypatch.setattr(selfinfo, "current", lambda: {"nickname": "星潮"})
    monkeypatch.setattr(selfinfo, "describe", lambda: "昵称：星潮")
    assert await plugindata._t_self_info(_Bot(), None, {}) == "昵称：星潮"


@pytest.mark.asyncio
async def test_mark_read_backs_off(monkeypatch):
    from src.plugins import interact

    calls = []

    class B:
        async def call_api(self, api, **kw):
            calls.append(api)
            raise RuntimeError("设置已读失败")

    monkeypatch.setattr(interact, "merged_whitelist", lambda: {1})
    monkeypatch.setattr(interact, "disabled_groups", lambda: set())
    monkeypatch.setattr(interact, "_read_failures", 0)
    monkeypatch.setattr(interact, "_read_skip", 0)
    for _ in range(10):
        await interact.mark_read_once(B())
    assert len(calls) == interact.READ_FAIL_LIMIT
