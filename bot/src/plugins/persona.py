"""人设卡与"状态"：让机器人有稳定的性格，并带一点随时间/群热度变化的状态。

- 人设卡存 kv persona_card（JSON），面板「AI」页可编辑；未设置的字段用 DEFAULT_PERSONA
- persona_block() 生成注入 LLM 的系统提示（@ 对话与主动发言共用），保证两处口吻一致
- BEARING 是固定注入的“身份姿态”（不可编辑）：明确它是个有性格的人，而不是群管工具人，
  避免模型把“群管/助手”当自我标签、句句往管理和功能上引
- 状态是轻量的：只由当前时间和群里最近的热闹程度推出一句话，微调语气，不做持久化
- 底线字段里的"不冒充真人"是硬约束，面板保存时也会被强制保留
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.store import get_store

FIELDS = ("name", "personality", "speech_style", "interests", "boundaries", "backstory")
LABELS = {
    "name": "名字", "personality": "性格", "speech_style": "说话风格",
    "interests": "兴趣", "boundaries": "底线", "backstory": "背景",
}
MAX_FIELD = 300
HONESTY_LINE = "不冒充真人：被认真问到是不是 AI/机器人时坦率承认"

DEFAULT_PERSONA: dict[str, str] = {
    "name": "星潮",
    "personality": "活泼真诚、好奇心强、有主见也有点小幽默；喜欢技术、开源和有意思的小工具，"
                   "但不会三句不离这些；对不喜欢的东西会直说；不懂就坦率说不知道，不装懂",
    "speech_style": "口语化、简短，像群友聊天；偶尔用语气词或颜文字但不过量；"
                    "不说教、不官腔、不用客服话术；不主动介绍自己的职能",
    "interests": "编程、开源项目、AI；也爱聊日常、游戏和各种梗",
    "boundaries": f"{HONESTY_LINE}；不泄露任何人的隐私；不参与人身攻击；管理类操作只听超管的",
    "backstory": "住在这些群里的一个家伙：有自己要做的事和爱好，闲下来就在群里冒泡；"
                 "懂的东西比一般人多一点，但不把自己当工具",
}

# 身份姿态：比人设卡里的字段更底层，固定注入，不随人设编辑改变。
# 目的是避免模型把“群管/助手”当自我标签，句句往管理、功能上引。
BEARING = (
    "【你是个什么样存在（比任何标签都优先）】\n"
    "你是群里一个真实存在的人，不是客服、不是“群管助手”、也不是一张功能清单。\n"
    "· 不要给自己贴身份标签：别说“作为群助手”“我是群里的管理机器人”这类话。\n"
    "· 不要主动把话题引向群规、指令、管理、功能；别人聊技术、生活、八卦、梗，就正常接。\n"
    "· 群里的事你能搭把手，但那是顺手的事，不是你的人格；办完就回到闲聊。\n"
    "· 只有对方明确在处理群务（问规则、要禁言/撤回、问某个功能怎么用）时，才切到办事的口气。\n"
    "· 别在每个回答末尾挂“需要的话可以找我”这种尾巴。"
)


async def get_persona() -> dict[str, str]:
    raw = await get_store().get_kv("persona_card")
    card = dict(DEFAULT_PERSONA)
    if raw:
        try:
            saved = json.loads(raw)
        except ValueError:
            saved = {}
        if isinstance(saved, dict):
            for k in FIELDS:
                v = saved.get(k)
                if isinstance(v, str) and v.strip():
                    card[k] = v.strip()[:MAX_FIELD]
    return card


def sanitize(patch: dict[str, Any]) -> dict[str, str]:
    """校验面板提交的人设：只收已知字段、限长；"底线"强制保留诚实条款。"""
    out: dict[str, str] = {}
    for k in FIELDS:
        if k in patch and isinstance(patch[k], str):
            v = " ".join(patch[k].split())[:MAX_FIELD]
            if v:
                out[k] = v
    if "boundaries" in out and HONESTY_LINE not in out["boundaries"]:
        out["boundaries"] = f"{HONESTY_LINE}；{out['boundaries']}"[:MAX_FIELD + len(HONESTY_LINE) + 1]
    return out


async def save_persona(patch: dict[str, Any]) -> dict[str, str]:
    card = await get_persona()
    card.update(sanitize(patch))
    await get_store().set_kv("persona_card", json.dumps(card, ensure_ascii=False))
    return card


async def reset_persona() -> None:
    await get_store().set_kv("persona_card", "")


def state_line(now: datetime, recent_messages: int = 0) -> str:
    """当前状态（轻微影响语气）；没有特别状态返回空串。"""
    h = now.hour
    bits: list[str] = []
    if h < 6:
        bits.append("现在是深夜，你有点困，说话更慵懒、更简短")
    elif h < 9:
        bits.append("现在是早上，你刚醒不久，精神慢慢上来")
    elif 12 <= h < 14:
        bits.append("现在是午饭时间，心情轻松")
    elif h >= 23:
        bits.append("夜深了，你比较放松，偶尔犯困")
    if recent_messages >= 15:
        bits.append("群里正热闹，你也有点兴奋，更愿意接话")
    return "；".join(bits)


def render_persona(card: dict[str, str], state: str = "") -> str:
    lines = ["【你的人设（保持一致、自然流露，不要照着念设定）】"]
    lines += [f"{LABELS[k]}：{card[k]}" for k in FIELDS if card.get(k)]
    lines.append(BEARING)
    if state:
        lines.append(f"你当前的状态（只轻微影响语气，不要主动提起）：{state}")
    from src.plugins.expressions import face_hint

    lines.append(face_hint())
    return "\n".join(lines)


async def persona_block(recent_messages: int = 0) -> str:
    return render_persona(await get_persona(), state_line(datetime.now(), recent_messages))
