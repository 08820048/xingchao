import pytest
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from src.plugins import appeals, ocr


@pytest.fixture(autouse=True)
def _reset(monkeypatch, store):
    monkeypatch.setattr(ocr, "get_store", lambda: store)
    ocr._cache.clear()
    ocr._unsupported = False
    yield
    ocr._unsupported = False


def test_parse_ocr_result_formats():
    assert ocr.parse_ocr_result({"texts": [{"text": "加微信"}, {"text": " 领福利 "}]}) == "加微信 领福利"
    assert ocr.parse_ocr_result([{"text": "甲"}, "乙", {"nope": 1}]) == "甲 乙"
    assert ocr.parse_ocr_result({"data": [{"text": "丙"}]}) == "丙"
    assert ocr.parse_ocr_result(None) == "" and ocr.parse_ocr_result("str") == ""
    assert len(ocr.parse_ocr_result([{"text": "x" * 900}])) == ocr.MAX_CHARS


class FakeBot:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    async def call_api(self, name, **kw):
        self.calls.append((name, kw))
        if self.exc:
            raise self.exc
        return self.result


def _img(file="a.jpg", url="https://x/a.jpg"):
    return MessageSegment("image", {"file": file, "url": url})


async def test_image_text_caches_and_joins():
    bot = FakeBot({"texts": [{"text": "加我微信"}]})
    seg = _img()
    assert await ocr.image_text(bot, seg) == "加我微信"
    assert await ocr.image_text(bot, seg) == "加我微信" and len(bot.calls) == 1  # 缓存命中
    assert bot.calls[0] == ("ocr_image", {"image": "a.jpg"})
    assert await ocr.images_text(bot, [seg, _img("b.jpg", "https://x/b.jpg"), _img("c.jpg")]) == "加我微信\n加我微信"
    assert len(bot.calls) == 2  # 只处理前 MAX_IMAGES 张，且第一张走缓存


async def test_unsupported_disables_ocr_and_stops_calling():
    bot = FakeBot(exc=ActionFailed(retcode=1404, message="不支持的Api"))
    assert await ocr.ocr_enabled()
    assert await ocr.image_text(bot, _img()) == ""
    assert not await ocr.ocr_enabled()
    assert len(bot.calls) == 1  # 第一次 1404 后不再尝试 url 备选；调用方之后因 ocr_enabled()==False 不再调用


async def test_other_errors_try_next_ref_then_give_up():
    bot = FakeBot(exc=RuntimeError("timeout"))
    assert await ocr.image_text(bot, _img()) == ""
    assert [c[1]["image"] for c in bot.calls] == ["a.jpg", "https://x/a.jpg"]
    assert await ocr.ocr_enabled()  # 临时错误不关闭功能


async def test_switch_off_via_kv(store):
    await store.set_kv("ocr_guard_enabled", "false")
    assert not await ocr.ocr_enabled()


async def test_appeal_original_shows_ocr_text():
    rec = {"source": "sensitive", "group_id": 5, "ts": "2026-11-10T09:00:00", "message_id": None,
           "reason": "图片文字: 加我微信领福利 | 判定为广告"}
    assert "识别出的文字：加我微信领福利" in await appeals.original_block(rec)
