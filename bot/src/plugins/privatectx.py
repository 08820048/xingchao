"""私聊上下文：让 AI 知道你在私聊里刚用过什么指令、机器人回了什么、收到过什么通知。

问题：私聊里用斜杠指令（/备份、/pending、/能力自检……）由指令插件直接处理，AI 的对话历史里没有这段往来；
你接着问"刚才那个结果是啥意思"，AI 一无所知。（群聊没有这个问题：感知缓冲会记录群里所有消息和机器人发言。）

做法（只对能和 AI 私聊的人，即超管）：
- 你发的指令消息 → 以 user 轮次写进该私聊的 AI 历史
- 机器人发给你的私聊消息（指令回复、入群申请 / 申诉 / 邀请等通知）→ 以 assistant 轮次写入，带【指令/通知回复】标记，
  让 AI 清楚这是指令输出而不是它自己刚说的话
- AI 自己的回复不重复记录：ai.handle_ai 里设了 AI_REPLYING 上下文变量，钩子见到它就跳过
- 历史只在内存里，重启清空，/ai clear 可清；每条最多 600 字
"""

from __future__ import annotations

from typing import Any

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.log import logger

TAG = "【指令/通知回复】"
MAX_CHARS = 600


def _superuser_ids() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


def is_command_text(text: str) -> bool:
    starts = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in starts)


def extract_private_text(api: str, data: dict[str, Any]) -> tuple[int, str] | None:
    """从机器人调用的发送 API 里取 (对方 QQ, 文本)；非私聊发送或没有文字返回 None。"""
    if api == "send_private_msg":
        uid = data.get("user_id")
    elif api == "send_msg" and data.get("message_type") == "private":
        uid = data.get("user_id")
    else:
        return None
    if not uid:
        return None
    msg = data.get("message")
    if isinstance(msg, str):
        text = msg
    elif isinstance(msg, Message):
        text = msg.extract_plain_text()
        if any(s.type == "image" for s in msg):
            text = f"{text}[图片]"
        elif any(s.type == "record" for s in msg):
            text = f"{text}[语音]"
    elif isinstance(msg, list):
        text = "".join(str(s.get("data", {}).get("text", "")) for s in msg if isinstance(s, dict))
    else:
        return None
    text = text.strip()
    return (int(uid), text[:MAX_CHARS]) if text else None


async def _remember(user_id: int, role: str, content: str) -> None:
    from src.plugins import ai

    rounds = await ai._kv("ai_ctx_rounds")
    ai._append_history(-int(user_id), role, content, int(rounds))


recorder = on_message(priority=0, block=False)


@recorder.handle()
async def handle_user_command(event: PrivateMessageEvent) -> None:
    if event.user_id not in _superuser_ids():
        return
    text = event.message.extract_plain_text().strip()
    if text and is_command_text(text):
        await _remember(event.user_id, "user", text[:MAX_CHARS])


@Bot.on_called_api
async def _record_private_sends(bot, exception, api, data, result) -> None:
    if exception is not None or not data:
        return
    try:
        from src.plugins import ai

        if ai.AI_REPLYING.get():
            return  # AI 自己的回复由 chat() 记录
        got = extract_private_text(api, data)
        if got and got[0] in _superuser_ids():
            await _remember(got[0], "assistant", f"{TAG}{got[1]}")
    except Exception:
        logger.debug("记录私聊往来失败", exc_info=True)
