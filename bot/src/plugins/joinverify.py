"""新人入群验证：新成员入群后要在限定时间内回答一道简单算术题，防广告号 / 机器号。

- 默认关闭。/入群验证 开|关；/入群验证 提醒|踢出（超时处理方式）；/入群验证 <分钟>（时限，1–60，默认 5）
  AI 工具：set_join_verify（超管）
- 流程：白名单群有人入群 → 群里 @ 新人出题（如 "3+8=?"）→ 新人在群里发出正确数字即通过
- 答案校验（严格）：只认「这条消息就是在报那个数」。容忍「答案是 11 哦」这类口语，
  但任何算式一律不通过——1+2+…+9、1e9、0x9、9.0、9的平方根 都算答错。
  （旧实现是「文本里有任意一个数字等于答案即通过」，等于门槛形同虚设）
- AI 放宽通道（joinverify_ai_pass，默认开）：真人也会答不上算术题，直接说「我进来找人做网站」
  这种来意的，问一次 AI 判断是不是真人说明了真实目的，是就放行、不计答错。
  只在「像人话」时才问（纯数字、算式不问），每人最多问 2 次，每群每日有额度
  （joinverify_ai_daily_limit，默认 30，0=关闭）。AI 任何异常都**不放行**——宁可交管理员，
  也不能因为它挂了而放机器人进来。
- 未通过有两种情形：超时没回答；或答错次数用完（发了数字但不对，最多 3 次，每次错都会 @ 提示剩余机会）
- 处罚前先公示：判定未通过后，机器人先在群里 @ 该成员说明原因和即将执行的处理，
  再给 30 秒宽限（期间答对仍可通过），到点才执行：
    * 提醒（默认）：私聊通知超管，由人决定
    * 踢出：移出群聊（需机器人是群管理员；成功后群里再说明一句，失败会通知超管）
- 不验证：机器人自己、超管；入群时已有的老成员不受影响
- 待验证名单只在内存里：机器人重启后，重启前入群、尚未验证的人不再追踪
- kv：joinverify_enabled / joinverify_action（notify|kick）/ joinverify_minutes
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from nonebot import on_command, on_message, on_notice
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import superuser_ids
from src.background import periodic
from src.permission import GROUP_WHITELIST, SUPERUSER
from src.store import get_store

DEFAULT_MINUTES = 5
SCAN_SECONDS = 5
MAX_ATTEMPTS = 3    # 答错几次算未通过
GRACE_SECONDS = 30  # 群里公示后到真正处罚之间的宽限

# AI 放宽通道：答不上算术题、但确实是真人说明来意的，也放行。
# 每题每人最多问 AI 几次（防刷），每群每天有总额度（控成本）。
AI_PASS_MAX_TRIES = 2
AI_PASS_DAILY_LIMIT = 30
AI_PASS_MIN_CHARS = 4   # 太短的一句话不值得送 AI（"12"、"0000" 这种）

_NUM = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")

# 答案解析：只认「这条消息就是在报那个数」，而不是「哪个数字碰巧等于答案」。
#
# 旧的实现是「文本里任意一个独立数字等于答案即通过」，于是任何含目标数字的表达式都能混过去：
# 答案 9 时回 1+2+3+4+5+6+7+8+9、1e9、0x9、9的平方根 之类全都能过——门槛形同虚设。
# 现在改成白名单：先去掉常见的口语填充（"答案是"、"哦"、"吧"…），
# 剩下的必须**整个就是**答案数字，一个别的字符都不许有。
_FILLER = re.compile(
    "|".join(sorted({
        "答案是", "答案", "结果", "我算的", "我算出来", "算出来", "我猜", "应该是", "应该",
        "等于", "就是", "大概是", "大概", "我觉得", "我认为", "是", "为", "了", "的",
        "吧", "哦", "噢", "呀", "啊", "呢", "嘛", "哈", "嗯", "呐", "咯", "啦", "噢耶",
        "请问", "回答", "我的答案", "我的", "我", "那", "这个", "这题", "题", "报",
    }, key=len, reverse=True))
)
# 允许出现的其它字符：空白与常见标点（语气/停顿，不影响语义）
_PUNCT = re.compile("[\\s！!？?。，,、～~…·:：;；\"'“”‘’]+")

# 表达式特征：出现这些就基本不是"报答案"，而是拿运算糊弄（保留用于提示与判错）
_EXPR = re.compile(
    r"[\^*/÷×√∑∏()（）\[\]{}%％]|\d\s*[+\-]\s*\d|\d\s*[eE]\s*[+\-]?\d|0[xX][0-9a-fA-F]+"
    r"|\d\s*[.．]\s*\d|平方|立方|根号|开方|次方|对数|阶乘|[a-zA-Z]\s*[\^)（(]"
)


@dataclass
class Pending:
    answer: int
    deadline: float
    question: str
    attempts: int = 0               # 已答错次数
    punish_at: float | None = None  # 已在群里公示未通过，到此时刻执行处罚
    reason: str = ""
    ai_tries: int = field(default=0)  # 已送 AI 复核次数（防刷）
    ai_pass_enabled: bool = True       # 出题时从配置快照，避免答题途中改配置导致不一致
    ai_daily_limit: int = AI_PASS_DAILY_LIMIT


_pending: dict[tuple[int, int], Pending] = {}


def make_question(rng: random.Random | None = None) -> tuple[str, int]:
    rng = rng or random
    a, b = rng.randint(2, 9), rng.randint(2, 9)
    if rng.random() < 0.5:
        return f"{a}+{b}", a + b
    a, b = max(a, b), min(a, b)
    return f"{a}-{b}", a - b


def normalize_answer(text: str) -> str:
    """归一化一条待校验的回答：全角转半角、去口语填充与标点，只留核心内容。"""
    import unicodedata

    text = unicodedata.normalize("NFKC", text or "")
    text = _FILLER.sub("", text)
    return _PUNCT.sub("", text)


def looks_like_expression(text: str) -> bool:
    """这条消息看起来是在用算式糊弄，而不是直接报答案。"""
    import unicodedata

    return bool(_EXPR.search(unicodedata.normalize("NFKC", text or "")))


def check_answer(text: str, answer: int) -> bool:
    """只有「整条消息就是在报这个数」才算通过。

    容忍「答案是 11 哦」这类口语写法，但不接受任何表达式：
    1+2+…+9、1e9、0x9、9.0、9的平方根 一律不通过。
    """
    core = normalize_answer(text)
    if not core.isdigit():
        return False          # 还有别的字符 → 是算式/文字，不是答案
    return int(core) == answer


def is_wrong_attempt(text: str, answer: int) -> bool:
    """发了一串数字但不对（或拿算式糊弄）：算一次答错。纯聊天没有数字则不算。"""
    if not _NUM.findall(text):
        return False
    return not check_answer(text, answer)


def due_for_warning(now: float) -> list[tuple[int, int]]:
    """超时、且还没公示过的人。"""
    return [k for k, p in _pending.items() if p.punish_at is None and p.deadline <= now]


def due_for_punish(now: float) -> list[tuple[int, int]]:
    """已公示、宽限已过的人。"""
    return [k for k, p in _pending.items() if p.punish_at is not None and p.punish_at <= now]


# ---------------------------------------------------------------- AI 放宽通道
#
# 算术题是防机器人的最低门槛，但真人也会答不上来：有的是手机端不会算，
# 有的直接说明来意（"我进来找人做个网站"）。这类人本来就该放进来，
# 一律按答错把人踢掉属于误杀。
#
# 做法：不是答案、但读起来像人在说话时，问一次 AI「这是不是真人说明了真实来意」。
# 判定失败一律不放行（fail-closed），宁可交管理员也不要放机器人进来。

_AI_PASS_SYSTEM = (
    "你是 QQ 群入群验证的复核助手。新成员本应回答一道算术题，但有人答不上来，"
    "会说明自己进群的来意。请判断这条消息是不是**真人**在说明**真实的入群目的**。\n"
    '只输出一个 JSON：{"pass": true|false, "reason": "10字以内中文理由"}\n'
    "判 true 的情形：说了具体来意（找人/请教/合作/求职/咨询/买东西/交流等），"
    "语气自然，像普通人打字。\n"
    "判 false 的情形：广告引流推销、纯乱码或复读题目、明显群发模板、"
    "只丢一个数字或算式、完全没有来意、看不懂在说什么。\n"
    "拿不准一律 false（宁可交给管理员）。"
)

_CJK = re.compile(r"[\u4e00-\u9fff]")


def looks_like_natural_text(text: str) -> bool:
    """这条消息像不像人在说话（而不是数字、算式、乱码）。

    只有像人话的才值得送 AI 复核；"12"、"0000"、"e^2" 这类直接按答错处理。
    """
    text = (text or "").strip()
    if len(text) < AI_PASS_MIN_CHARS:
        return False
    if looks_like_expression(text):
        return False          # 拿算式糊弄，不送 AI，直接算答错
    if len(_CJK.findall(text)) >= 2:
        return True
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return letters >= 8       # 纯英文也允许，但要有一定长度


async def _ai_budget_ok(group_id: int, limit: int) -> bool:
    """每群每天最多问 AI 多少次；返回 False 表示今天额度用完。"""
    if limit <= 0:
        return False
    store = get_store()
    day = datetime.now().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"joinverify_ai_{group_id}") or ""
    used_day, _, used = raw.partition(":")
    count = int(used) if used_day == day and used.isdigit() else 0
    if count >= limit:
        return False
    await store.set_kv(f"joinverify_ai_{group_id}", f"{day}:{count + 1}")
    return True


async def ai_judge_genuine(text: str) -> tuple[bool, str]:
    """问 AI：这条消息是不是真人说明了真实来意。返回 (是否放行, 理由)。

    任何异常都返回 False —— 复核通道是「多放人进来」的便利，
    绝不能因为它挂掉而放机器人进来。
    """
    from src.plugins import ai as ai_plugin

    try:
        client = await ai_plugin.get_client()
        if client is None:
            return False, "AI 不可用"
        cfg = await ai_plugin.ai_config()
        completion = await client.chat.completions.create(
            model=cfg["ai_model"],
            messages=[
                {"role": "system", "content": _AI_PASS_SYSTEM},
                {"role": "user", "content": f"新成员的消息：{text[:300]}"},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("入群验证 AI 复核调用失败")
        return False, "AI 调用失败"

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return False, "AI 返回不可解析"
    try:
        data = json.loads(m.group())
    except ValueError:
        return False, "AI 返回不可解析"
    if not isinstance(data, dict):
        return False, "AI 返回不可解析"
    passed = data.get("pass") is True
    reason = str(data.get("reason") or "")[:40]
    return passed, reason


async def try_ai_pass(group_id: int, p: "Pending", text: str) -> tuple[bool, str]:
    """走一遍放宽通道；命中返回 (True, 理由)，否则 (False, 原因)。"""
    if not p.ai_pass_enabled:
        return False, "未开启"
    if p.ai_tries >= AI_PASS_MAX_TRIES:
        return False, "本人已用满复核次数"
    if not looks_like_natural_text(text):
        return False, "不像人话"
    p.ai_tries += 1
    if not await _ai_budget_ok(group_id, p.ai_daily_limit):
        return False, "本群今日复核额度已用完"
    passed, reason = await ai_judge_genuine(text)
    if passed:
        logger.info(f"入群验证 AI 放行：group={group_id} 理由={reason} 文本={text[:60]}")
    return passed, reason


async def _cfg() -> dict:
    store = get_store()
    try:
        minutes = int(await store.get_kv("joinverify_minutes") or DEFAULT_MINUTES)
    except ValueError:
        minutes = DEFAULT_MINUTES
    try:
        ai_limit = int(await store.get_kv("joinverify_ai_daily_limit") or AI_PASS_DAILY_LIMIT)
    except ValueError:
        ai_limit = AI_PASS_DAILY_LIMIT
    return {
        "enabled": (await store.get_kv("joinverify_enabled")) == "true",
        "action": "kick" if (await store.get_kv("joinverify_action")) == "kick" else "notify",
        "minutes": min(60, max(1, minutes)),
        # 默认开启：答不上算术题但说明了来意的真人应该放进来
        "ai_pass": (await store.get_kv("joinverify_ai_pass")) != "false",
        "ai_daily_limit": max(0, min(ai_limit, 10000)),
    }


async def set_config(
    enabled: bool | None = None, action: str | None = None, minutes: int | None = None,
    ai_pass: bool | None = None, ai_daily_limit: int | None = None,
) -> str:
    store = get_store()
    if enabled is not None:
        await store.set_kv("joinverify_enabled", "true" if enabled else "false")
    if action in ("notify", "kick"):
        await store.set_kv("joinverify_action", action)
    if minutes is not None:
        await store.set_kv("joinverify_minutes", str(min(60, max(1, int(minutes)))))
    if ai_pass is not None:
        await store.set_kv("joinverify_ai_pass", "true" if ai_pass else "false")
    if ai_daily_limit is not None:
        await store.set_kv("joinverify_ai_daily_limit", str(max(0, min(int(ai_daily_limit), 10000))))
    c = await _cfg()
    return (
        f"入群验证：{'开启' if c['enabled'] else '关闭'}"
        f"｜超时处理：{'踢出' if c['action'] == 'kick' else '仅提醒超管'}"
        f"｜时限 {c['minutes']} 分钟"
        f"｜AI 复核：{'开启' if c['ai_pass'] else '关闭'}"
        f"（每群每日 {c['ai_daily_limit']} 次）"
    )


# ---------------------------------------------------------------- 事件

join_notice = on_notice(rule=GROUP_WHITELIST, priority=4, block=False)


@join_notice.handle()
async def handle_join(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    uid = event.user_id
    if uid == int(bot.self_id) or uid in superuser_ids():
        return
    cfg = await _cfg()
    if not cfg["enabled"]:
        return
    q, ans = make_question()
    _pending[(event.group_id, uid)] = Pending(
        ans, time.time() + cfg["minutes"] * 60, q,
        ai_pass_enabled=cfg["ai_pass"], ai_daily_limit=cfg["ai_daily_limit"],
    )
    tail = "未通过会被请出群聊" if cfg["action"] == "kick" else "未通过会通知管理员"
    try:
        await bot.call_api(
            "send_group_msg", group_id=event.group_id,
            message=(
                MessageSegment.at(uid)
                + f" 欢迎！为防广告号，请在 {cfg['minutes']} 分钟内回复 {q} 的结果"
                f"（发数字即可，共 {MAX_ATTEMPTS} 次作答机会）"
                + ("；算不出来就直接说说你进群想做什么，也行" if cfg["ai_pass"] else "")
                + f"，{tail}。"
            ),
        )
    except Exception:
        logger.exception("发送入群验证题失败")


verify_msg = on_message(rule=GROUP_WHITELIST, priority=3, block=False)


async def _say(bot: Bot, group_id: int, user_id: int, text: str) -> None:
    try:
        await bot.call_api("send_group_msg", group_id=group_id,
                           message=MessageSegment.at(user_id) + " " + text)
    except Exception:
        logger.exception("入群验证：群内通知发送失败")


async def _say_plain(bot: Bot, group_id: int, text: str) -> None:
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=text)
    except Exception:
        logger.exception("入群验证：群内通知发送失败")


def wrong_answer_hint(question: str, left: int, text: str) -> str:
    """答错时的提示文案。用算式糊弄时额外说明「直接回数字就行」。"""
    if looks_like_expression(text):
        return (
            f"不用算，直接回复 {question} 的结果数字就行（还有 {left} 次机会）"
        )
    return f"答案不对哦，请再算一次 {question}（还有 {left} 次机会）"


@verify_msg.handle()
async def handle_answer(bot: Bot, event: GroupMessageEvent) -> None:
    key = (event.group_id, event.user_id)
    p = _pending.get(key)
    if p is None:
        return
    text = event.message.extract_plain_text()
    if check_answer(text, p.answer):  # 宽限期内答对同样通过
        _pending.pop(key, None)
        await _say(bot, event.group_id, event.user_id, "验证通过，玩得开心～")
        return
    # 放宽通道：答不上算术题、但确实在说明来意的真人，也放行（不走答错计数）
    if p.punish_at is None and text.strip():
        passed, reason = await try_ai_pass(event.group_id, p, text)
        if passed:
            _pending.pop(key, None)
            await _say(
                bot, event.group_id, event.user_id,
                "验证通过，欢迎～（看得出你是真的想进群，先放你进来啦）",
            )
            return
        if reason not in ("未开启", "不像人话"):
            # 送过 AI 但没通过：如实说明，别让人反复试
            logger.info(
                f"入群验证 AI 未放行：group={event.group_id} user={event.user_id} 原因={reason}"
            )

    if p.punish_at is None and is_wrong_attempt(text, p.answer):
        p.attempts += 1
        if p.attempts >= MAX_ATTEMPTS:
            await announce_failure(bot, key, f"答错了 {MAX_ATTEMPTS} 次")
        else:
            await _say(
                bot, event.group_id, event.user_id,
                wrong_answer_hint(p.question, MAX_ATTEMPTS - p.attempts, text),
            )


# ---------------------------------------------------------------- 未通过：先公示，再处罚

async def announce_failure(bot: Bot, key: tuple[int, int], reason: str, now: float | None = None) -> None:
    """判定未通过：在群里 @ 该成员说明原因和即将执行的处理，并开始宽限倒计时。"""
    p = _pending.get(key)
    if p is None or p.punish_at is not None:
        return
    now = time.time() if now is None else now
    p.punish_at = now + GRACE_SECONDS
    p.reason = reason
    gid, uid = key
    what = "被移出群聊" if (await _cfg())["action"] == "kick" else "交由管理员处理"
    await _say(bot, gid, uid,
               f"入群验证未通过（{reason}）。{GRACE_SECONDS} 秒后将{what}；"
               f"现在回复 {p.question} 的结果还来得及。")


async def punish(bot: Bot, key: tuple[int, int], action: str) -> None:
    from src.plugins.appeals import notify_superusers

    p = _pending.pop(key, None)
    gid, uid = key
    reason = p.reason if p else "未通过"
    if action == "kick":
        try:
            await bot.call_api("set_group_kick", group_id=gid, user_id=uid, reject_add_request=False)
        except Exception as e:
            await _say(bot, gid, uid, "入群验证未通过，但我没有权限移出你，已通知管理员处理。")
            await notify_superusers(
                bot, f"⚠️ 入群验证未通过：{uid}（群 {gid}，{reason}），但踢出失败：{str(getattr(e, 'info', None) or e)[:60]}")
            return
        await _say_plain(bot, gid, f"{uid} 入群验证未通过（{reason}），已移出群聊。")
        await notify_superusers(bot, f"🧹 入群验证未通过：已将 {uid} 移出群 {gid}（{reason}）")
    else:
        await notify_superusers(bot, f"⚠️ 入群验证未通过：{uid}（群 {gid}，{reason}），请管理员决定是否处理")


async def handle_timeouts(bot: Bot, now: float | None = None) -> int:
    """每次扫描：超时的先公示；已公示且宽限已过的执行处罚。返回本次处理的人数。"""
    now = time.time() if now is None else now
    warn, punish_keys = due_for_warning(now), due_for_punish(now)
    if not (warn or punish_keys):
        return 0
    for key in warn:
        await announce_failure(bot, key, "超时未回答", now)
    if punish_keys:
        action = (await _cfg())["action"]
        for key in punish_keys:
            await punish(bot, key, action)
    return len(warn) + len(punish_keys)


@periodic("入群验证超时扫描", SCAN_SECONDS)
async def _scan(bot: Bot) -> None:
    await handle_timeouts(bot)


# ---------------------------------------------------------------- 指令

verify_cmd = on_command("入群验证", rule=SUPERUSER, priority=1, block=True)


@verify_cmd.handle()
async def handle_cmd(matcher: Matcher, args: Message = CommandArg()) -> None:
    arg = args.extract_plain_text().strip()
    if arg in ("开", "on"):
        await matcher.finish(await set_config(enabled=True))
    if arg in ("关", "off"):
        await matcher.finish(await set_config(enabled=False))
    if arg in ("提醒", "notify"):
        await matcher.finish(await set_config(action="notify"))
    if arg in ("踢出", "kick"):
        await matcher.finish(await set_config(action="kick"))
    if arg in ("复核", "ai"):
        c = await _cfg()
        await matcher.finish(await set_config(ai_pass=not c["ai_pass"]))
    if arg.isdigit():
        await matcher.finish(await set_config(minutes=int(arg)))
    await matcher.finish(
        (await set_config())
        + "\n用法：/入群验证 开|关｜提醒|踢出｜复核（AI 复核开关）｜<分钟>"
    )
