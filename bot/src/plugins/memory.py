"""长期记忆：机器人对群友的"印象"和对群的"记忆"，让它能说出"你上次说的那个 bug 修好了吗"。

存什么：模型从聊天里提炼的**短事实**（兴趣、技术栈、在做的项目、称呼偏好、群里的梗…），
每人最多 8 条、每条 ≤40 字；不存聊天原话。按群隔离（A 群里了解到的不会出现在 B 群）。
怎么来：后台每 5 分钟巡检；某群新增足够多的发言、且距上次整理够久时，把这段聊天 + 已有记忆
交给模型更新一次（有每日次数上限）。输入来自 awareness 的内存缓冲。
怎么用：@ 对话与主动发言前，把当前相关的几个人的印象注入提示（有字数预算），并附"熟悉度"。
隐私与控制（硬约束）：
  - 成员 /忘记我 → 清除其在所有群的印象并**停止记录**（发言也不再送去整理），/记住我 恢复
  - /记忆 随时查看机器人记得自己什么（私聊里会列出所有群）
  - 提炼结果有本地过滤：联系方式、证件、账号密码、健康/财务/政治等敏感内容一律丢弃
  - 超管可在面板「AI」页查看、编辑、删除；memory_enabled 总开关
配置（kv）：memory_enabled / memory_min_new_messages / memory_interval_minutes /
memory_daily_limit（见 DEFAULTS）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.rule import Rule

from src.permission import disabled_groups, merged_whitelist
from src.store import get_store

DEFAULTS: dict[str, Any] = {
    "memory_enabled": True,
    "memory_min_new_messages": 20,
    "memory_interval_minutes": 30,
    "memory_daily_limit": 8,
}
USER_FACTS_MAX = 8
GROUP_FACTS_MAX = 12
FACT_MAX_CHARS = 40
BLOCK_BUDGET = 900      # 注入提示的总字数预算
SCAN_SECONDS = 300
MAX_TRANSCRIPT_LINES = 60

# 本地敏感过滤：命中任一规则的"事实"直接丢弃（不依赖模型自觉）
_SENSITIVE_RE = re.compile(
    r"(\d{6,}|https?://|www\.|@[\w.-]+\.\w+|密码|口令|验证码|身份证|护照|手机号|电话|微信号|住址|家庭地址|"
    r"银行卡|余额|工资|薪资|存款|负债|确诊|抑郁|焦虑症|病史|怀孕|性取向|出轨|党员|政治|宗教|"
    r"未成年|初中生|小学生|真名|真实姓名)"
)

_last_ts: dict[int, float] = {}
_daily: dict[tuple[int, str], int] = {}
_worker_started = False


# ---------------------------------------------------------------- 纯函数

def clean_facts(raw: Any, limit: int) -> list[str]:
    """校验模型给出的事实列表：去重、限长、丢弃敏感内容。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        fact = " ".join(item.replace("\n", " ").split()).lstrip("-•· ").strip()
        if not (2 <= len(fact) <= FACT_MAX_CHARS) or _SENSITIVE_RE.search(fact) or fact in out:
            continue
        out.append(fact)
        if len(out) >= limit:
            break
    return out


def familiarity(interactions: int) -> str:
    if interactions >= 15:
        return "很熟"
    if interactions >= 3:
        return "认识"
    return "刚认识"


def parse_json_object(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_update(
    old_users: dict[int, list[str]], old_group: list[str], data: dict, speakers: set[int],
) -> tuple[dict[int, list[str]], list[str]]:
    """把模型输出合并进已有记忆。只接受本轮发言人；清洗后为空则保留旧记忆（防止误清空）。"""
    users = dict(old_users)
    for key, facts in (data.get("users") or {}).items():
        try:
            uid = int(key)
        except (TypeError, ValueError):
            continue
        if uid not in speakers:
            continue
        cleaned = clean_facts(facts, USER_FACTS_MAX)
        if cleaned or not users.get(uid):
            users[uid] = cleaned
    group = clean_facts(data.get("group"), GROUP_FACTS_MAX) or old_group
    return users, group


# ---------------------------------------------------------------- 配置

async def config() -> dict[str, Any]:
    store = get_store()
    cfg: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = await store.get_kv(key)
        if raw is None:
            cfg[key] = default
        elif isinstance(default, bool):
            cfg[key] = raw != "false"
        else:
            try:
                cfg[key] = max(1, int(raw))
            except (TypeError, ValueError):
                cfg[key] = default
    return cfg


async def memory_enabled() -> bool:
    return bool((await config())["memory_enabled"])


# ---------------------------------------------------------------- 整理（后台）

CONSOLIDATE_RULES = (
    "你在为 QQ 群机器人「星潮」整理“记忆”。下面是群里最近的聊天和你已有的记忆，请更新记忆，"
    "只输出一个 JSON（不要代码块和多余文字）：\n"
    '{"users": {"QQ号": ["事实", ...]}, "group": ["事实", ...]}\n'
    "规则：\n"
    "- 只记录对话中明确说出、长期有用的内容：兴趣爱好、技术栈或在做的项目、称呼偏好、"
    "与机器人的约定、群里的梗/常聊话题/近期大事。\n"
    f"- 每人最多 {USER_FACTS_MAX} 条、群记忆最多 {GROUP_FACTS_MAX} 条，每条不超过 30 字，用第三人称短句，如“在学 Rust”。\n"
    "- 绝对不要记录：联系方式、真实姓名、住址、证件、账号密码、健康/财务/感情/政治/宗教/性相关、"
    "未成年人个人信息、别人转述的隐私、对某人的负面评价；也不要逐字摘抄聊天。\n"
    "- 只依据聊天里明确的内容，不要猜测；过时或被推翻的旧记忆要删掉；没有新信息就原样返回旧记忆。\n"
    "- users 里只能出现下面列出的成员 QQ。\n"
)


def build_consolidation_prompt(
    transcript: str, members: dict[int, str], old_users: dict[int, list[str]], old_group: list[str],
) -> str:
    member_lines = "\n".join(f"{uid} = {name}" for uid, name in members.items())
    old = {"users": {str(u): f for u, f in old_users.items() if f}, "group": old_group}
    return (
        f"{CONSOLIDATE_RULES}\n【成员】\n{member_lines}\n\n【已有记忆】\n"
        f"{json.dumps(old, ensure_ascii=False)}\n\n【最近的聊天（名字(QQ): 内容）】\n{transcript}"
    )


def _daily_used(gid: int) -> int:
    day = datetime.now().strftime("%Y-%m-%d")
    for k in [k for k in _daily if k[1] != day]:
        _daily.pop(k, None)
    return _daily.get((gid, day), 0)


async def consolidate_group(gid: int, *, force: bool = False) -> str:
    """整理一个群的记忆；返回结果说明（updated / skipped:原因）。"""
    from src.plugins import ai as ai_plugin
    from src.plugins import awareness

    cfg = await config()
    if not cfg["memory_enabled"]:
        return "skipped:disabled"
    optouts = await get_store().memory_optouts()
    since = _last_ts.get(gid, 0.0)
    entries = [
        e for e in awareness._buffers.get(gid, ())
        if not e["bot"] and e["ts"] > since and e["user_id"] not in optouts
        and (e["text"] or any(i["caption"] for i in e["images"]))
    ]
    if not force:
        if len(entries) < cfg["memory_min_new_messages"]:
            return "skipped:few-messages"
        if time.time() - since < cfg["memory_interval_minutes"] * 60 and since:
            return "skipped:interval"
        if _daily_used(gid) >= cfg["memory_daily_limit"]:
            return "skipped:daily-limit"
    if not entries:
        return "skipped:no-messages"

    members: dict[int, str] = {}
    lines: list[str] = []
    for e in entries[-MAX_TRANSCRIPT_LINES:]:
        members[e["user_id"]] = e["name"]
        text = e["text"][:100]
        caps = [f"[图片:{i['caption']}]" for i in e["images"] if i["caption"]]
        lines.append(f"{e['name']}({e['user_id']}): {' '.join([text, *caps]).strip()}")
    store = get_store()
    old_users: dict[int, list[str]] = {}
    for uid in members:
        m = await store.get_memory("user", gid, uid)
        old_users[uid] = m["facts"] if m else []
    gm = await store.get_memory("group", gid)
    old_group = gm["facts"] if gm else []

    raw = await ai_plugin.generate_text(
        build_consolidation_prompt("\n".join(lines), members, old_users, old_group),
        system="你是严谨的记忆整理助手，只输出 JSON，宁缺毋滥，尊重隐私。",
        temperature=0.2,
        max_tokens=2500,
    )
    _daily[(gid, datetime.now().strftime("%Y-%m-%d"))] = _daily_used(gid) + 1
    data = parse_json_object(raw or "")
    if data is None:
        return "skipped:bad-output"
    users, group = apply_update(old_users, old_group, data, set(members))
    for uid, facts in users.items():
        if facts != old_users.get(uid):
            await store.set_memory_facts("user", gid, uid, facts)
    if group != old_group:
        await store.set_memory_facts("group", gid, 0, group)
    _last_ts[gid] = max(e["ts"] for e in entries)
    return "updated"


async def _worker() -> None:
    from src.plugins import ai as ai_plugin

    logger.info("长期记忆整理器已启动（每 5 分钟巡检）")
    while True:
        try:
            if await ai_plugin.is_ai_enabled() and await memory_enabled():
                for gid in sorted(merged_whitelist() - disabled_groups()):
                    result = await consolidate_group(gid)
                    if result == "updated":
                        logger.info(f"群 {gid} 记忆已整理")
        except Exception:
            logger.exception("记忆整理异常")
        await asyncio.sleep(SCAN_SECONDS)


@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_worker())


# ---------------------------------------------------------------- 注入

def participants(group_id: int, asker: int | None, limit: int = 6) -> tuple[list[int], dict[int, str]]:
    """当前对话相关的人：提问者在前，其次是最近发言的人。"""
    from src.plugins import awareness

    names: dict[int, str] = {}
    order: list[int] = [asker] if asker else []
    for e in reversed(list(awareness._buffers.get(group_id, ()))):
        if e["bot"]:
            continue
        names.setdefault(e["user_id"], e["name"])
        if e["user_id"] not in order:
            order.append(e["user_id"])
    return order[:limit], names


MEMORY_HEADER = (
    "【你对群友的了解（长期记忆，来自过往聊天的提炼）】\n"
    "使用方式：自然地运用，像老朋友那样偶尔提起（如关心对方在做的事），不要刻意炫耀“我记得”，"
    "不要念清单；对方问你记得什么就如实说；涉及隐私或对方没提起的事不要主动翻出来；"
    "记忆可能过时，拿不准就当作问句来问。\n"
)


async def memory_block(group_id: int, user_ids: list[int], names: dict[int, str]) -> str:
    """生成注入 LLM 的记忆提示；没有可用记忆返回空串。"""
    if not await memory_enabled():
        return ""
    store = get_store()
    optouts = await store.memory_optouts()
    lines: list[str] = []
    for uid in user_ids:
        if uid in optouts:
            continue
        m = await store.get_memory("user", group_id, uid)
        if not m or not (m["facts"] or m["interactions"]):
            continue
        label = f"{names.get(uid, uid)}（{familiarity(m['interactions'])}）"
        lines.append(f"- {label}：" + ("；".join(m["facts"]) if m["facts"] else "（暂无特别印象）"))
    gm = await store.get_memory("group", group_id)
    if gm and gm["facts"]:
        lines.append("- 本群：" + "；".join(gm["facts"]))
    if not lines:
        return ""
    body = "\n".join(lines)
    while len(body) > BLOCK_BUDGET and "\n" in body:
        body = body.rsplit("\n", 1)[0]
    return MEMORY_HEADER + body[:BLOCK_BUDGET]


async def note_interaction(group_id: int, user_id: int) -> None:
    """机器人与某人互动一次（用于熟悉度）；已退出记忆的成员不记。"""
    try:
        if not await memory_enabled() or user_id in await get_store().memory_optouts():
            return
        await get_store().bump_interactions(group_id, user_id)
    except Exception:
        logger.debug("记录互动次数失败", exc_info=True)


# ---------------------------------------------------------------- 指令（所有成员）

async def _allowed(event: MessageEvent) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return True
    return (
        isinstance(event, GroupMessageEvent)
        and event.group_id in merged_whitelist()
        and event.group_id not in disabled_groups()
    )


_rule = Rule(_allowed)
recall_cmd = on_command("记忆", aliases={"我的记忆"}, rule=_rule, priority=1, block=True)
forget_cmd = on_command("忘记我", aliases={"清除记忆"}, rule=_rule, priority=1, block=True)
resume_cmd = on_command("记住我", rule=_rule, priority=1, block=True)


def _format_facts(facts: list[str], interactions: int) -> str:
    return (("；".join(facts)) if facts else "（还没有特别的印象）") + f"　熟悉度：{familiarity(interactions)}"


async def describe_memory(user_id: int, group_id: int | None) -> str:
    store = get_store()
    if user_id in await store.memory_optouts():
        return "你已选择不被记住，我不会记录你的任何信息。想恢复发送 /记住我。"
    if group_id is not None:
        m = await store.get_memory("user", group_id, user_id)
        body = _format_facts(m["facts"], m["interactions"]) if m else "（还没有特别的印象）"
        return f"我对你的印象（仅本群）：\n{body}\n发送 /忘记我 可清除并让我不再记录。"
    rows = await store.list_user_memories(user_id)
    if not rows:
        return "我还没有记住关于你的任何内容。"
    lines = [f"群 {r['group_id']}：{_format_facts(r['facts'], r['interactions'])}" for r in rows]
    return "我对你的印象：\n" + "\n".join(lines) + "\n发送 /忘记我 可全部清除并让我不再记录。"


@recall_cmd.handle()
async def handle_recall(matcher: Matcher, event: MessageEvent) -> None:
    gid = event.group_id if isinstance(event, GroupMessageEvent) else None
    await matcher.send(await describe_memory(event.user_id, gid))


@forget_cmd.handle()
async def handle_forget(matcher: Matcher, event: MessageEvent) -> None:
    store = get_store()
    removed = await store.delete_user_memories(event.user_id)
    await store.set_memory_optout(event.user_id, True)
    await matcher.send(
        f"好的，已清除我对你的全部印象（{removed} 个群），之后也不会再记录你的发言。"
        "想恢复发送 /记住我。"
    )


@resume_cmd.handle()
async def handle_resume(matcher: Matcher, event: MessageEvent) -> None:
    store = get_store()
    if event.user_id not in await store.memory_optouts():
        await matcher.send("我本来就在记着你呀，发送 /记忆 看看我记得什么。")
        return
    await store.set_memory_optout(event.user_id, False)
    await matcher.send("好～之后我会重新慢慢了解你。发送 /记忆 可以随时查看，/忘记我 可以随时清除。")
