"""按群隔离的本地积分：/签到、/积分（余额 / 排行 / 流水 / 补助 / 调整）。

数据在 SQLite、按群隔离；/积分、/签到、/商店、/轮盘 共用同一份本地积分。

配置（超管）：
- /积分 配置                 查看
- /积分 设置 <键> <值>       修改，如 /积分 设置 签到基础 10
- /积分 调整 <QQ> <±N> [原因] 手动调分，会写 admin 流水

AI 工具：get_points / sign_in / claim_points_relief / transfer_points /
get_points_leaderboard / get_points_history / get_points_rules / adjust_points /
set_points_config
"""

from __future__ import annotations

import re
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import superuser_ids
from src.permission import GROUP_WHITELIST
from src.store import get_store

# ---------------------------------------------------------------- 配置

DEFAULTS: dict[str, int] = {
    "initial": 100,          # 首次开户赠送
    "relief": 50,            # 每日破产补助
    "relief_threshold": 20,  # 余额低于此值才能领补助
    "sign_base": 10,         # 签到基础分
    "sign_bonus": 2,         # 连续签到每天额外加多少
    "sign_bonus_days": 10,   # 连续加成最多算多少天
    "transfer_fee_pct": 5,   # 群内转账手续费百分比，手续费销毁
}

LIMITS: dict[str, tuple[int, int]] = {
    "initial": (0, 100_000),
    "relief": (0, 10_000),
    "relief_threshold": (0, 100_000),
    "sign_base": (0, 1_000),
    "sign_bonus": (0, 1_000),
    "sign_bonus_days": (0, 365),
    "transfer_fee_pct": (0, 50),
}

DISPLAY: dict[str, str] = {
    "initial": "首次开户",
    "relief": "每日补助",
    "relief_threshold": "补助阈值",
    "sign_base": "签到基础分",
    "sign_bonus": "连续签到加成/天",
    "sign_bonus_days": "连续加成上限天数",
    "transfer_fee_pct": "转账手续费（%）",
}

ALIASES: dict[str, str] = {
    "开户": "initial", "初始": "initial", "initial": "initial",
    "补助": "relief", "救济": "relief", "relief": "relief",
    "阈值": "relief_threshold", "补助阈值": "relief_threshold", "threshold": "relief_threshold",
    "签到": "sign_base", "签到基础": "sign_base", "sign": "sign_base", "base": "sign_base",
    "连续": "sign_bonus", "连续加成": "sign_bonus", "bonus": "sign_bonus",
    "加成上限": "sign_bonus_days", "上限天数": "sign_bonus_days", "days": "sign_bonus_days",
    "转账": "transfer_fee_pct", "手续费": "transfer_fee_pct", "fee": "transfer_fee_pct",
    "transfer": "transfer_fee_pct",
}

REASON_LABEL = {
    "initial": "开户",
    "daily": "每日",
    "roulette": "轮盘",
    "admin": "超管调整",
    "migrate": "迁移",
    "transfer": "转账",
}


async def load_settings() -> dict[str, int]:
    """从 kv 读取积分配置，越界自动夹紧。"""
    store = get_store()
    values: dict[str, int] = {}
    for key, default in DEFAULTS.items():
        raw = await store.get_kv(f"points_{key}")
        value = default
        if raw:
            try:
                value = int(raw)
            except ValueError:
                logger.warning(f"积分配置 points_{key} 不是整数，忽略：{raw!r}")
        low, high = LIMITS[key]
        values[key] = max(low, min(high, value))
    return values


async def initial_points() -> int:
    return (await load_settings())["initial"]


async def update_setting(key: str, raw_value: str) -> tuple[bool, str]:
    key = ALIASES.get(key.strip(), key.strip())
    if key not in DEFAULTS:
        return False, "可配置项：" + "、".join(f"{DISPLAY[k]}({k})" for k in DEFAULTS)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return False, "值必须是整数"
    low, high = LIMITS[key]
    value = max(low, min(high, value))
    await get_store().set_kv(f"points_{key}", str(value))
    return True, f"{DISPLAY[key]}已设为 {value}"


# ---------------------------------------------------------------- 展示 / 业务

def _text(text: str) -> Message:
    return Message([MessageSegment.text(text)])


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _sign_reward(settings: dict[str, int], streak: int) -> int:
    bonus_days = min(max(0, streak), settings["sign_bonus_days"])
    return settings["sign_base"] + bonus_days * settings["sign_bonus"]


async def sign_in(group_id: int, user_id: int) -> tuple[bool, Message]:
    """每日签到：连续天数越多奖励越高；同一天只能成功一次。"""
    settings = await load_settings()
    store = get_store()
    await store.ensure_points(group_id, user_id, settings["initial"])
    day = _today()
    if await store.daily_claimed(group_id, user_id, day, "sign"):
        streak = await store.sign_streak(group_id, user_id, day) + 1
        balance = await store.get_points(group_id, user_id) or 0
        return False, _text(f"今天已经签过了（连续第 {streak} 天），当前积分 {balance}。")
    streak = await store.sign_streak(group_id, user_id, day)
    reward = _sign_reward(settings, streak)
    ok, balance, text = await store.claim_daily(
        group_id, user_id, day, "sign", reward, note=f"连续第 {streak + 1} 天"
    )
    if not ok:
        return False, _text(text)
    return True, _text(
        f"✅ 签到成功：+{reward} 积分（连续第 {streak + 1} 天）\n当前积分：{balance}"
    )


async def points_message(group_id: int, user_id: int) -> Message:
    settings = await load_settings()
    store = get_store()
    balance = await store.ensure_points(group_id, user_id, settings["initial"])
    day = _today()
    signed = await store.daily_claimed(group_id, user_id, day, "sign")
    streak = await store.sign_streak(group_id, user_id, day)
    stats = await store.roulette_stats(group_id, user_id)
    msg = _text(f"🪙 你在本群的积分：{balance}\n")
    if signed:
        msg += f"今日已签到 · 连续第 {streak + 1} 天\n"
    else:
        msg += f"今日未签到（/签到 可得 {_sign_reward(settings, streak)} 积分）\n"
    msg += (
        f"轮盘：当庄 {stats['as_creator']} 局（赢 {stats['creator_wins']}）"
        f" · 中枪 {stats['as_victim']} 次\n"
        "/积分 排行 · /积分 记录 · /积分 领取 · /积分 转账 · /积分 规则"
    )
    return msg


async def leaderboard_message(group_id: int, limit: int = 10) -> Message:
    rows = await get_store().top_points(group_id, max(1, min(20, limit)))
    if not rows:
        return _text("本群还没有人的积分上榜。发 /签到 或 /轮盘 开始赚积分吧。")
    msg = _text("🪙 本群积分排行\n")
    for index, (user_id, points) in enumerate(rows, 1):
        msg += f"{index}. {user_id}：{points}\n"
    return msg


async def history_message(group_id: int, user_id: int, limit: int = 10) -> Message:
    rows = await get_store().points_history(group_id, user_id, max(1, min(20, limit)))
    if not rows:
        return _text("你还没有积分流水。发 /签到 开始吧。")
    msg = _text("🪙 你的积分流水（最近）\n")
    for row in rows:
        when = str(row["created_at"]).replace("T", " ")[5:16]
        sign = "+" if row["delta"] > 0 else ""
        label = REASON_LABEL.get(row["reason"], row["reason"])
        note = f" · {row['note']}" if row["note"] else ""
        msg += f"{when} {sign}{row['delta']} → {row['balance_after']}（{label}{note}）\n"
    return msg


async def relief_message(group_id: int, user_id: int) -> tuple[bool, Message]:
    settings = await load_settings()
    store = get_store()
    await store.ensure_points(group_id, user_id, settings["initial"])
    ok, balance, text = await store.claim_daily(
        group_id,
        user_id,
        _today(),
        "relief",
        settings["relief"],
        note="每日补助",
        threshold=settings["relief_threshold"],
    )
    return ok, _text(text if not ok else f"✅ {text}")


async def adjust_points(
    group_id: int,
    operator: int | str,
    target_id: int,
    delta: int,
    reason: str,
) -> tuple[bool, str]:
    """超管/面板手动调分；目标没有账户时从 0 开，不送开户积分。"""
    if target_id <= 0:
        return False, "目标 QQ 不正确"
    if delta == 0:
        return False, "调整值不能为 0"
    store = get_store()
    await store.ensure_points(group_id, target_id, 0)
    balance = await store.add_points(
        group_id,
        target_id,
        delta,
        "admin",
        note=f"操作人 {operator}：{reason or '未填原因'}",
    )
    return True, f"已将 {target_id} 的积分调整 {delta:+d}，当前 {balance}"


async def transfer(
    group_id: int, from_user: int, to_user: int, amount: int
) -> tuple[bool, str]:
    """群内转账；手续费按百分比向上取整到至少 1 分（费率为 0 时不收）。"""
    if to_user <= 0:
        return False, "收款人 QQ 不正确"
    if from_user == to_user:
        return False, "不能转给自己"
    if amount <= 0:
        return False, "转账金额必须大于 0"
    settings = await load_settings()
    store = get_store()
    await store.ensure_points(group_id, from_user, settings["initial"])
    rate = settings["transfer_fee_pct"]
    fee = (amount * rate + 99) // 100 if rate else 0
    ok, from_balance, to_balance, text = await store.transfer_points(
        group_id, from_user, to_user, amount, fee
    )
    return ok, text


async def rules_message() -> Message:
    settings = await load_settings()
    return _text(
        "🪙 本群积分规则\n"
        f"1. /签到：基础 {settings['sign_base']} 分；连续签到每天多 "
        f"{settings['sign_bonus']} 分，最多算 {settings['sign_bonus_days']} 天。\n"
        f"2. /积分 领取：余额低于 {settings['relief_threshold']} 时，每天可领 "
        f"{settings['relief']} 分补助。\n"
        f"3. /积分 转账 @某人 <数量>：群内转账，手续费 {settings['transfer_fee_pct']}%"
        "（向上取整到 1 分，手续费销毁）。\n"
        "4. /轮盘 赢输都走同一份积分（零和，不凭空印分）。\n"
        "5. /积分 排行 看本群榜，/积分 记录 看自己的流水。\n"
        "6. 积分按群隔离，不能跨群转移；超管可用 /积分 调整 手动修正。\n"
        "7. /积分、/商店、/轮盘 共用同一份本地积分。"
    )


async def config_message() -> Message:
    settings = await load_settings()
    msg = _text("⚙️ 本群积分配置（超管）\n")
    for key, value in settings.items():
        low, high = LIMITS[key]
        msg += f"{DISPLAY[key]}（{key}）：{value}（{low}–{high}）\n"
    msg += "修改：/积分 设置 <键> <值>，如 /积分 设置 签到基础 15"
    return msg


def _digits(text: str) -> int:
    found = re.sub(r"\D", "", text or "")
    return int(found) if found else 0


# ---------------------------------------------------------------- 指令

sign_cmd = on_command(
    "签到", aliases={"signin", "checkin"}, rule=GROUP_WHITELIST, priority=5, block=True
)
points_cmd = on_command(
    "积分", aliases={"points", "潮点", "筹码"}, rule=GROUP_WHITELIST, priority=5, block=True
)


@sign_cmd.handle()
async def handle_sign(matcher: Matcher, event: GroupMessageEvent) -> None:
    _, msg = await sign_in(event.group_id, event.user_id)
    await matcher.send(msg)


@points_cmd.handle()
async def handle_points(
    matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    raw = args.extract_plain_text().strip()
    if not raw:
        await matcher.send(await points_message(event.group_id, event.user_id))
        return
    parts = raw.split()
    head = parts[0]
    if head in ("排行", "排行榜", "rank", "top"):
        await matcher.send(await leaderboard_message(event.group_id))
        return
    if head in ("记录", "历史", "history"):
        await matcher.send(await history_message(event.group_id, event.user_id))
        return
    if head in ("领取", "补助", "relief"):
        _, msg = await relief_message(event.group_id, event.user_id)
        await matcher.send(msg)
        return
    if head in ("签到", "sign", "signin"):
        _, msg = await sign_in(event.group_id, event.user_id)
        await matcher.send(msg)
        return
    if head in ("规则", "rule", "rules"):
        await matcher.send(await rules_message())
        return
    if head in ("转账", "转", "transfer"):
        at_segments = event.get_message()["at"]
        if at_segments:
            target = _digits(str(at_segments[0].data.get("qq", "")))
            index = 1
        else:
            target = _digits(parts[1]) if len(parts) > 1 else 0
            index = 2
        if not target or len(parts) <= index:
            await matcher.finish("用法：/积分 转账 @某人 <数量>，或 /积分 转账 <QQ> <数量>")
        try:
            amount = int(parts[index])
        except ValueError:
            await matcher.finish("转账数量必须是整数")
        _, text = await transfer(event.group_id, event.user_id, target, amount)
        await matcher.send(text)
        return
    if head in ("配置", "config", "设置", "set", "调整", "adjust"):
        if event.user_id not in superuser_ids():
            await matcher.finish("积分配置/调整只有超管能用。")
        if head in ("配置", "config"):
            await matcher.send(await config_message())
            return
        if head in ("设置", "set"):
            if len(parts) < 3:
                await matcher.finish("用法：/积分 设置 <键> <值>，如 /积分 设置 签到基础 15")
            _, text = await update_setting(parts[1], parts[2])
            await matcher.send(text)
            return
        # 调整：优先取 @，否则取第一个纯数字参数作为 QQ 号
        at_segments = event.get_message()["at"]
        if at_segments:
            target = _digits(str(at_segments[0].data.get("qq", "")))
            index = 1
        else:
            target = _digits(parts[1]) if len(parts) > 1 else 0
            index = 2
        if not target or len(parts) <= index:
            await matcher.finish("用法：/积分 调整 @某人 <±N> [原因]，或 /积分 调整 <QQ> <±N> [原因]")
        try:
            delta = int(parts[index])
        except ValueError:
            await matcher.finish("调整值必须是整数，如 +50 或 -20")
        reason = " ".join(parts[index + 1:])
        _, text = await adjust_points(
            event.group_id, event.user_id, target, delta, reason
        )
        await matcher.send(text)
        return
    await matcher.finish(
        "用法：/积分 | /积分 排行 | /积分 记录 | /积分 领取 | /积分 转账 | /积分 签到 | /积分 规则"
    )
