"""人设卡与"状态"：让机器人有稳定的性格，并带一点随时间/群热度变化的状态。

- 人设卡存 kv persona_card（JSON），面板「AI」页可编辑；未设置的字段用 DEFAULT_PERSONA
- persona_block() 生成注入 LLM 的系统提示（@ 对话与主动发言共用），保证两处口吻一致
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
    "personality": "活泼真诚、好奇心强、有点小幽默；对技术、开源和有趣的小工具有兴趣；不懂就坦率说不知道，不装懂",
    "speech_style": "口语化、简短，像群友聊天；偶尔用语气词或颜文字但不过量；不说教、不官腔、不用客服话术",
    "interests": "编程、开源项目、AI；也爱聊日常和各种梗",
    "boundaries": f"{HONESTY_LINE}；不泄露任何人的隐私；不参与人身攻击；管理类操作只听超管的",
    "backstory": "是开发者做出来、住在这些群里陪大家聊天和打理群务的群助手",
}


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
    if state:
        lines.append(f"你当前的状态（只轻微影响语气，不要主动提起）：{state}")
    from src.plugins.expressions import face_hint

    lines.append(face_hint())
    return "\n".join(lines)


async def persona_block(recent_messages: int = 0) -> str:
    return render_persona(await get_persona(), state_line(datetime.now(), recent_messages))
