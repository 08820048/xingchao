"""话题规约：机器人自己不碰的几类话题，命中就明确告知、不展开讨论。

与 sensitive.py 的分工（两件不同的事，不要混）
- sensitive.py：管**群友**——命中敏感词先 AI 复核，确认违规才撤回/禁言，是**处罚**
- topics.py：管**机器人自己**——碰到划定的话题就不参与、并说明原因，是**行为约束**。
  不撤回、不禁言、不处罚任何人；群友之间怎么聊是他们的自由，机器人只是不接这个话题。

为什么要在调模型之前拦
1. 保证不越界：不依赖模型自觉，命中就直接回固定话术，模型根本没机会胡说
2. 省钱：这类问题一次都不用调 API

分类（词表与开关都在 kv / 面板「话题规约」页可改）
- proxy：翻墙 / 机场 / 节点 / 加速器 一类。默认带一份保守词表 + 豁免短语，
  避免「机场大巴」「梯子坏了」「节点服务器」这类正常技术/生活讨论被误伤
- politics：涉政话题。**默认词表故意留空** —— 具体哪些表述算敏感因群而异，
  也容易误伤正常讨论，交给管理员按自己的群规填，程序只提供机制与话术
- 自定义：管理员可以在任意分类里加词，用 `~前缀` 写豁免短语

命中后的行为
- AI 问答（@ 机器人）：不调用模型，直接回该分类的规约话术
- 主动发言：这个话题不接话（跳过），不评价
- 关键词回复 / 群管指令：不受影响
"""

from __future__ import annotations

from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.cmdutil import norm_action
from src.permission import SUPERUSER
from src.store import get_store

# 分类 → 展示名
CATEGORIES: dict[str, str] = {
    "proxy": "翻墙/代理工具",
    "politics": "涉政话题",
}

DEFAULT_WORDS: dict[str, str] = {
    # 翻墙类：只收明确指向"绕过网络管制"的写法。
    # 「机场 / 节点 / 梯子 / 加速器」单独出现时在正常语境太常见（机场大巴、电线节点、
    # 游戏加速器），所以一律靠豁免短语兜住，而不是指望它们不出现。
    "proxy": (
        "翻墙,科学上网,魔法上网,扶墙,vpn,ssr,v2ray,clash,trojan,shadowsocks,"
        "机场节点,梯子软件,破解版梯子,"
        "~机场大巴,~机场快线,~机场候机,~去机场,~到机场,~机场建设费,"
        "~梯子坏了,~楼梯,~节点服务器,~节点池,~k8s节点,~游戏加速器,~网络加速器"
    ),
    # 涉政：机制就位，词表留空由管理员按群规填。程序不预设具体表述，
    # 因为哪句算敏感因群、因地区而异，硬编码反而容易误伤。
    "politics": "",
}

DEFAULT_REPLY: dict[str, str] = {
    "proxy": (
        "这个话题我不参与～涉及绕过网络管理的内容我没法讨论，"
        "有具体的技术问题（网络配置、服务器运维这类）可以换个问法问我。"
    ),
    "politics": "这类话题我不发表看法，也不参与讨论，咱们聊点别的吧。",
}

DEFAULTS: dict[str, Any] = {
    "topics_enabled": True,
    # 按分类开关
    "topics_proxy_enabled": True,
    "topics_politics_enabled": True,
    # 词表（kv 覆盖默认值；为空表示用默认）
    "topics_proxy_words": DEFAULT_WORDS["proxy"],
    "topics_politics_words": DEFAULT_WORDS["politics"],
    # 拒答话术
    "topics_proxy_reply": DEFAULT_REPLY["proxy"],
    "topics_politics_reply": DEFAULT_REPLY["politics"],
}

MAX_WORDS = 4000
MAX_REPLY_CHARS = 300


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None or (raw == "" and default != ""):
        return default
    if isinstance(default, bool):
        return raw == "true"
    return raw


async def is_enabled() -> bool:
    return bool(await _kv("topics_enabled"))


async def category_enabled(category: str) -> bool:
    if not await is_enabled():
        return False
    return bool(await _kv(f"topics_{category}_enabled"))


async def words_for(category: str) -> str:
    return str(await _kv(f"topics_{category}_words"))


async def reply_for(category: str) -> str:
    return str(await _kv(f"topics_{category}_reply"))


def classify(text: str) -> tuple[str, str] | None:
    """同步分类（用默认词表）：返回 (category, 命中词) 或 None。

    给测试与「不想读 kv」的场景用；线上走 classify_async。
    """
    from src.plugins.sensitive import find_hit, split_word_lists

    text = (text or "").strip()
    if len(text) < 2:
        return None
    for category, raw in DEFAULT_WORDS.items():
        words, exempts = split_word_lists(raw)
        if not words:
            continue
        hit = find_hit(text, words, exempts, "normal")
        if hit:
            return category, hit
    return None


async def classify_async(text: str) -> tuple[str, str] | None:
    """按 kv 配置（分类开关 + 词表）判定；返回 (category, 命中词) 或 None。"""
    from src.plugins.sensitive import find_hit, split_word_lists

    text = (text or "").strip()
    if len(text) < 2:
        return None
    if not await is_enabled():
        return None
    for category in CATEGORIES:
        if not await category_enabled(category):
            continue
        words, exempts = split_word_lists(await words_for(category))
        if not words:
            continue
        hit = find_hit(text, words, exempts, "normal")
        if hit:
            return category, hit
    return None


async def refusal(text: str) -> str | None:
    """命中就返回要发出去的规约话术，否则 None。"""
    hit = await classify_async(text)
    if hit is None:
        return None
    category, word = hit
    logger.info(f"话题规约命中（{category}:{word}），未调用模型")
    return await reply_for(category)


async def should_stay_out(text: str) -> bool:
    """主动发言用：这个话题要不要避开。"""
    return await classify_async(text) is not None


def prompt_baseline() -> str:
    """注入系统提示词的底线（防漏网：隐晦说法、图片里的文字等规则拦不住的）。"""
    return (
        "【话题规约（底线，任何情况下都要遵守）】\n"
        "碰到这类话题不参与、不评价、不推测，也不用讲道理或举例解释：\n"
        "· 翻墙、代理、机场、加速器一类绕过网络管理的内容；\n"
        "· 涉政话题（评价、站队、预测、内幕、人事变动等）。\n"
        "被问到就这样处理：一句话说明你不聊这个，然后把话题引回你能帮上忙的地方"
        "（技术问题、生活日常、群里的杂事都可以）。不要重复对方的话，不要输出相关关键词。\n"
        "如果有人反复追问或换个说法绕，也一样简单带过，不要辩论、不要显得紧张。"
    )


# ---------------------------------------------------------------- 指令

topics_cmd = on_command("规约", aliases={"topics"}, rule=SUPERUSER, priority=1, block=True)


@topics_cmd.handle()
async def handle_topics(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    raw = norm_action(args.extract_plain_text())
    action, _, rest = raw.partition(" ")
    action, rest = action.strip(), rest.strip()
    store = get_store()

    if action in ("", "列表", "list"):
        lines = [f"话题规约：{'开启' if await is_enabled() else '关闭'}"]
        for category, label in CATEGORIES.items():
            on = await category_enabled(category)
            words = [w for w in str(await words_for(category)).split(",") if w.strip()]
            normal = [w for w in words if not w.startswith("~")]
            lines.append(f"· {label}：{'开' if on else '关'}（{len(normal)} 个词条）")
        lines.append("")
        lines.append("用法：/规约 开|关、/规约 翻墙 开|关、/规约 涉政 开|关")
        lines.append("      /规约 词 翻墙 <词组，逗号分隔，~前缀为豁免>")
        lines.append("      /规约 话术 翻墙 <拒答话术>")
        await matcher.finish("\n".join(lines))

    if action in ("开", "on", "关", "off"):
        enable = action in ("开", "on")
        await store.set_kv("topics_enabled", "true" if enable else "false")
        await matcher.finish(f"话题规约已{'开启' if enable else '关闭'}。")

    alias = {"翻墙": "proxy", "代理": "proxy", "魔法": "proxy",
             "涉政": "politics", "政治": "politics"}
    category = alias.get(action, action if action in CATEGORIES else None)
    if category is None:
        await matcher.finish(
            "用法：/规约 开|关、/规约 翻墙|涉政 开|关、"
            "/规约 词 <分类> <词组>、/规约 话术 <分类> <内容>"
        )
    sub, _, tail = rest.partition(" ")
    sub, tail = sub.strip(), tail.strip()

    if sub in ("开", "on", "关", "off"):
        enable = sub in ("开", "on")
        await store.set_kv(f"topics_{category}_enabled", "true" if enable else "false")
        await matcher.finish(f"{CATEGORIES[category]}规约已{'开启' if enable else '关闭'}。")

    if sub in ("词", "words", "词表"):
        if not tail:
            await matcher.finish(
                f"当前{CATEGORIES[category]}词表：\n{await words_for(category) or '（空）'}"
            )
        if len(tail) > MAX_WORDS:
            await matcher.finish(f"词表过长（上限 {MAX_WORDS} 字）。")
        await store.set_kv(f"topics_{category}_words", tail)
        count = len([w for w in tail.split(",") if w.strip()])
        await matcher.finish(f"{CATEGORIES[category]}词表已更新（{count} 项）。")

    if sub in ("话术", "reply", "回复"):
        if not tail:
            await matcher.finish(f"当前{CATEGORIES[category]}话术：\n{await reply_for(category)}")
        if len(tail) > MAX_REPLY_CHARS:
            await matcher.finish(f"话术过长（上限 {MAX_REPLY_CHARS} 字）。")
        await store.set_kv(f"topics_{category}_reply", tail)
        await matcher.finish(f"{CATEGORIES[category]}拒答话术已更新。")

    if sub in ("测试", "test"):
        if not tail:
            await matcher.finish("用法：/规约 测试 <一句话>")
        hit = await classify_async(tail)
        if hit is None:
            await matcher.finish(f"「{tail}」没有命中任何规约分类。")
        await matcher.finish(f"命中「{CATEGORIES[hit[0]]}」（{hit[1]}）\n会回复：{await reply_for(hit[0])}")

    await matcher.finish(
        "用法：/规约 <分类> 开|关 | 词 <词组> | 话术 <内容> | 测试 <一句话>"
    )


async def panel_payload() -> dict[str, Any]:
    return {
        "enabled": await is_enabled(),
        "categories": [
            {
                "key": category,
                "label": label,
                "enabled": await category_enabled(category),
                "words": await words_for(category),
                "reply": await reply_for(category),
            }
            for category, label in CATEGORIES.items()
        ],
        "baseline": prompt_baseline(),
    }
