"""群内互动小能力：点赞每日一次、戳一戳冷却、群文件检索与格式化、签名截断、长回复转发记录。"""

import time

import pytest

from src.plugins import awareness as aw
from src.plugins import interact


class FakeBot:
    self_id = "100"

    def __init__(self, replies=None, fail=()):
        self.calls = []
        self.replies = replies or {}
        self.fail = set(fail)

    async def call_api(self, api, **kw):
        self.calls.append((api, kw))
        if api in self.fail:
            raise RuntimeError("boom")
        return self.replies.get(api)


@pytest.fixture(autouse=True)
def _reset():
    interact._liked.clear()
    interact._last_poke.clear()


async def test_like_once_per_day():
    bot = FakeBot()
    ok, _ = await interact.give_like(bot, 1)
    assert ok and bot.calls[0] == ("send_like", {"user_id": 1, "times": interact.LIKE_TIMES})
    ok, msg = await interact.give_like(bot, 1)
    assert not ok and "已经" in msg and len(bot.calls) == 1


async def test_like_failure_not_counted():
    bot = FakeBot(fail={"send_like"})
    ok, msg = await interact.give_like(bot, 2)
    assert not ok and "失败" in msg
    assert 2 not in interact._liked


async def test_poke_cooldown():
    bot = FakeBot()
    assert (await interact.poke(bot, 9, 1))[0]
    ok, msg = await interact.poke(bot, 9, 2)
    assert not ok and "稍等" in msg
    interact._last_poke[9] = time.monotonic() - 100
    assert (await interact.poke(bot, 9, 2))[0]


async def test_files_keyword_search_includes_subfolders():
    bot = FakeBot(replies={
        "get_group_root_files": {"files": [{"file_name": "readme.md", "size": 2048, "modify_time": 1}],
                                 "folders": [{"folder_id": "f1"}]},
        "get_group_files_by_folder": {"files": [{"file_name": "周报.pdf", "size": 3 * 1024 * 1024, "modify_time": 5,
                                                 "file_id": "x", "busid": 102, "uploader_name": "小明"}]},
        "get_group_file_url": {"url": "https://example.com/a"},
    })
    text = await interact.files_text(bot, 1, "周报")
    assert "周报.pdf" in text and "3.0MB" in text and "https://example.com/a" in text
    assert "readme" not in text


async def test_files_empty_and_error():
    assert "空" in await interact.files_text(FakeBot(replies={"get_group_root_files": {"files": []}}), 1)
    assert "失败" in await interact.files_text(FakeBot(fail={"get_group_root_files"}), 1)


async def test_signature_truncated_and_cleared():
    bot = FakeBot()
    ok, _ = await interact.set_signature(bot, "x" * 200)
    assert ok and len(bot.calls[0][1]["longNick"]) == interact.SIGNATURE_MAX
    ok, msg = await interact.set_signature(bot, "")
    assert ok and "清空" in msg


async def test_mark_read_stops_when_unsupported(monkeypatch):
    monkeypatch.setattr(interact, "merged_whitelist", lambda: {1, 2, 3})
    monkeypatch.setattr(interact, "disabled_groups", lambda: set())
    bot = FakeBot(fail={"mark_group_msg_as_read"})
    assert await interact.mark_read_once(bot) == 0
    assert len(bot.calls) == 1  # 第一个群失败就停，不逐群重试


def test_forward_send_text_is_recorded():
    nodes = [{"type": "node", "data": {"name": "星潮", "uin": "1", "content": "很长的回答"}}]
    assert aw.extract_bot_text("send_group_forward_msg", {"group_id": 5, "messages": nodes}) == (5, "很长的回答")
    assert aw.extract_bot_text("send_group_forward_msg", {"group_id": 5}) == (5, "[发了一组合并转发]")
