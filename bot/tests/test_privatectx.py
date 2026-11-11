"""私聊上下文：指令与指令回复进入 AI 历史，AI 自己的回复不重复记录。"""

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins import ai, privatectx


def test_extract_private_text():
    ex = privatectx.extract_private_text
    assert ex("send_private_msg", {"user_id": 1, "message": "hi"}) == (1, "hi")
    assert ex("send_msg", {"message_type": "private", "user_id": 2, "message": Message("你好")}) == (2, "你好")
    assert ex("send_msg", {"message_type": "group", "group_id": 3, "message": "x"}) is None
    assert ex("send_group_msg", {"group_id": 3, "message": "x"}) is None
    assert ex("send_private_msg", {"user_id": 1, "message": "  "}) is None
    img = Message([MessageSegment.text("看"), MessageSegment.image("a.jpg")])
    assert ex("send_private_msg", {"user_id": 1, "message": img}) == (1, "看[图片]")
    long = ex("send_private_msg", {"user_id": 1, "message": "x" * 2000})
    assert len(long[1]) == privatectx.MAX_CHARS


@pytest.fixture
def hist(store, monkeypatch):
    monkeypatch.setattr(ai, "get_store", lambda: store)
    ai._ctx.clear()
    yield
    ai._ctx.clear()


async def test_remember_uses_private_key_and_rounds(hist):
    await privatectx._remember(42, "user", "/备份")
    await privatectx._remember(42, "assistant", privatectx.TAG + "已备份")
    assert ai._ctx[-42] == [
        {"role": "user", "content": "/备份"},
        {"role": "assistant", "content": "【指令/通知回复】已备份"},
    ]


async def test_hook_skips_ai_replies_and_non_superusers(hist, monkeypatch):
    monkeypatch.setattr(privatectx, "_superuser_ids", lambda: {42})
    call = privatectx._record_private_sends
    await call(None, None, "send_private_msg", {"user_id": 42, "message": "指令输出"}, None)
    assert ai._ctx[-42][-1]["content"] == "【指令/通知回复】指令输出"

    token = ai.AI_REPLYING.set(True)
    try:
        await call(None, None, "send_private_msg", {"user_id": 42, "message": "AI 的话"}, None)
    finally:
        ai.AI_REPLYING.reset(token)
    assert len(ai._ctx[-42]) == 1  # AI 回复没有重复写入

    await call(None, None, "send_private_msg", {"user_id": 99, "message": "给普通用户"}, None)
    assert -99 not in ai._ctx
    await call(None, RuntimeError("fail"), "send_private_msg", {"user_id": 42, "message": "失败的发送"}, None)
    assert len(ai._ctx[-42]) == 1
