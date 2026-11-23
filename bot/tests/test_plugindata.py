import pytest

from src.aitools import build_tools, plugindata


def test_tool_registered():
    assert "query_points" in {t[0]["function"]["name"] for t in build_tools()}


def test_clean_reply_strips_cq():
    assert plugindata.clean_reply("[CQ:image,file=a.png]") == ""
    assert plugindata.clean_reply("你的积分：50 [CQ:face,id=1]") == "你的积分：50"


class _Ev:
    user_id = 1
    group_id = 9
    sender = type("S", (), {"nickname": "n"})()


class _Bot:
    self_id = "100"


@pytest.mark.asyncio
async def test_private_chat_rejected():
    ev = type("P", (), {"user_id": 1})()
    assert "群里" in await plugindata._t_query_points(_Bot(), ev, {"query": "积分"})


@pytest.mark.asyncio
async def test_write_commands_rejected():
    for q in ("签到", "转账 1 100", "挖矿"):
        assert "只支持" in await plugindata._t_query_points(_Bot(), _Ev(), {"query": q})


@pytest.mark.asyncio
async def test_query_passes_target_and_returns_text(monkeypatch):
    seen = {}

    class C:
        async def command_request(self, command, qq, qun, botqq, at=0, **extra):
            seen.update(command=command, qq=qq, qun=qun, botqq=botqq, at=at)
            return "积分：50"

    monkeypatch.setattr(plugindata, "_client", lambda: C())
    out = await plugindata._t_query_points(_Bot(), _Ev(), {"query": "积分", "target_qq": 2})
    assert out == "积分：50"
    assert seen == {"command": "积分", "qq": 1, "qun": 9, "botqq": 100, "at": 2}


@pytest.mark.asyncio
async def test_no_plugin_or_server_down(monkeypatch):
    monkeypatch.setattr(plugindata, "_client", lambda: None)
    assert "没有启用" in await plugindata._t_query_points(_Bot(), _Ev(), {"query": "积分"})

    class Bad:
        async def command_request(self, *a, **k):
            raise RuntimeError("x")

    monkeypatch.setattr(plugindata, "_client", lambda: Bad())
    assert "连不上" in await plugindata._t_query_points(_Bot(), _Ev(), {"query": "积分"})
