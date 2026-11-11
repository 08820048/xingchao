from nonebot.adapters.onebot.v11.exception import ActionFailed

from src.plugins import capabilities as cap


class FakeBot:
    def __init__(self, behaviors):
        self.behaviors, self.calls = behaviors, []

    async def call_api(self, name, **kw):
        self.calls.append(name)
        b = self.behaviors.get(name, {"ok": True})
        if isinstance(b, Exception):
            raise b
        return b


def test_classify_failure():
    assert cap.classify_failure(ActionFailed(retcode=1404, message="不支持的Api")) == cap.UNSUPPORTED
    assert cap.classify_failure(ActionFailed(retcode=200, message="Error: no permission")) == cap.ERROR
    assert cap.classify_failure(RuntimeError("Unsupported action x")) == cap.UNSUPPORTED
    assert cap.classify_failure(RuntimeError("timeout")) == cap.ERROR


async def test_probe_and_report(store, monkeypatch):
    monkeypatch.setattr(cap, "get_store", lambda: store)
    bot = FakeBot({
        "get_version_info": {"app_name": "NapCat.Onebot", "app_version": "4.1.0"},
        "get_essence_msg_list": [{"a": 1}, {"a": 2}],
        "fetch_custom_face": ActionFailed(retcode=1404, message="不支持的Api"),
        "get_group_honor_info": ActionFailed(retcode=100, message="bot is not admin"),
    })
    results = await cap.probe(bot, 123)
    by = {r["api"]: r for r in results}
    assert by["get_version_info"]["detail"] == "NapCat.Onebot 4.1.0"
    assert by["get_essence_msg_list"]["detail"] == "2 条"
    assert by["fetch_custom_face"]["status"] == cap.UNSUPPORTED
    assert by["get_group_honor_info"]["status"] == cap.ERROR
    assert all(c in [p[1] for p in cap.PROBES] for c in bot.calls) and len(bot.calls) == len(cap.PROBES)  # 全是只读探测
    text = cap.format_report(results, 123)
    assert "❌ 收藏表情包" in text and "⚠️ 群荣誉" in text and "表情包库" in text
    await cap.run_and_store(bot, 123)
    assert await cap.supported("fetch_custom_face") is False
    assert await cap.supported("get_essence_msg_list") is True
    assert await cap.supported("never_probed") is None
