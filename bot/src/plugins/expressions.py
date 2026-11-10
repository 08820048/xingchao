"""表情表达：QQ 系统表情（face）与收藏表情包库，让机器人说话时有表情、能发表情包。

- 系统表情：AI 在文字里写 [表情:笑哭]，发送前换成 QQ 系统表情（face 消息段）；
  语音回复前要先 strip_faces，避免把占位符念出来
- 表情回应（给某条消息贴表情）的 ID 也用系统表情 ID，见 FACES / proactive.EMOJIS
- ID 对照取自 QQ 系统表情常见编号；个别 ID 若与实际图案不符，只会显示成别的小表情，无害，
  可在 FACES 里直接改
"""

from __future__ import annotations

import re

from nonebot.adapters.onebot.v11 import Message, MessageSegment

FACES: dict[str, int] = {
    "微笑": 14, "呲牙": 13, "可爱": 21, "笑哭": 182, "斜眼笑": 178, "捂脸": 264, "流泪": 5,
    "大哭": 9, "委屈": 106, "无奈": 174, "疑问": 32, "问号": 268, "暗中观察": 269, "吃瓜": 271,
    "汪汪": 277, "喵喵": 307, "卖萌": 175, "赞": 76, "OK": 124, "握手": 78, "抱拳": 118,
    "鼓掌": 99, "庆祝": 320, "比心": 319, "爱心": 66, "加油": 315, "牛啊": 299, "emm": 270,
}
MAX_FACES_PER_MESSAGE = 2
_TOKEN = re.compile(r"\[表情[:：]\s*([^\]\s]{1,8})\s*\]")


def face_hint() -> str:
    """给模型看的用法说明（并入人设块）。"""
    return (
        "表情：说话时偶尔可以用系统表情，写成 [表情:名称]（一条消息最多 1–2 个，别滥用，没必要就不用）。"
        "可用名称：" + "、".join(FACES) + "。"
    )


def strip_faces(text: str) -> str:
    """去掉所有表情占位符（语音合成、纯文本场景用）。"""
    return _TOKEN.sub("", text).strip()


def render_faces(text: str, max_faces: int = MAX_FACES_PER_MESSAGE) -> Message:
    """把文字里的 [表情:名称] 换成 face 消息段；不认识的名称、超出数量的占位符直接去掉。"""
    msg = Message()
    pos, used = 0, 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            msg += MessageSegment.text(text[pos:m.start()])
        fid = FACES.get(m.group(1))
        if fid is not None and used < max_faces:
            msg += MessageSegment.face(fid)
            used += 1
        pos = m.end()
    if pos < len(text):
        msg += MessageSegment.text(text[pos:])
    return msg if len(msg) else Message([MessageSegment.text("")])
