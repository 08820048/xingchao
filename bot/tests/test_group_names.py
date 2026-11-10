from src.plugins import panel


class FakeBot:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, 0

    async def call_api(self, name, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return self.rows


async def test_group_names_fetch_cache_and_fallback(monkeypatch):
    panel._GROUP_NAMES.update(ts=0.0, map={})
    bot = FakeBot([{"group_id": 1, "group_name": "测试群"}, {"group_id": 2}])
    monkeypatch.setattr(panel, "_panel_bot", lambda: bot)
    assert await panel.group_names() == {1: "测试群", 2: ""}
    await panel.group_names()
    assert bot.calls == 1  # 缓存命中
    bot.rows = [{"group_id": 3, "group_name": "新群"}]
    assert await panel.group_names(force=True) == {3: "新群"}
    # 接口失败 / 机器人未连接：沿用上次结果，不抛异常
    monkeypatch.setattr(panel, "_panel_bot", lambda: FakeBot(fail=True))
    assert await panel.group_names(force=True) == {3: "新群"}
    monkeypatch.setattr(panel, "_panel_bot", lambda: None)
    assert await panel.group_names(force=True) == {3: "新群"}
