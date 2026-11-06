"""群满员提醒与成员清理。

- 检测：有新成员入群时、以及每 30 分钟巡检一次白名单群；成员数 ≥ 群上限即视为满员
- 提醒：满员后在群里发一条通知（同一群 24 小时内只提醒一次）
- 清理：按活跃度倒排，取最不活跃的前 N 人（默认 100）
    * 数据来自 QQ 自带的群成员资料（NapCat get_group_member_list）：最后发言时间 last_sent_time、
      群等级 level（QQ 按活跃度累计），对机器人入群之前的历史同样有效；
      再叠加机器人自己统计的近 N 天（默认 30）发言数作为补充
    * 排序：近 N 天无发言（QQ 口径）的人优先 → 机器人统计发言数少 → 群等级低 → 最后发言更早 → 入群更早
    * 保护名单：群主/管理员、机器人自己、超管、入群不满 7 天的新人
- 默认只「通知超管 + 给出待清理名单」，由超管发 `/清理群员 [群号] 确认` 才真正执行；
  kv groupfull_auto_kick=true 时满员后自动执行（需机器人是群管理员）
- 开关与参数（kv）：groupfull_enabled(默认 true) / groupfull_auto_kick(默认 false) /
  groupfull_kick_count(100) / groupfull_activity_days(30) / groupfull_protect_days(7)
- 指令（超管）：/清理群员 [群号]  预览；/清理群员 [群号] 确认  执行
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from nonebot import get_bots, get_driver, on_command, on_notice
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER, disabled_groups, merged_whitelist
from src.store import get_store

CHECK_INTERVAL = 30 * 60
REMIND_COOLDOWN = 24 * 3600
KICK_GAP_SECONDS = 0.6
MAX_KICK_COUNT = 200
PREVIEW_LINES = 20
_last_check: dict[int, float] = {}  # 同一群两次检测的最小间隔，避免入群潮里反复调 API
_running: set[int] = set()  # 正在清理的群，防止并发重复执行
_driver = get_driver()
_worker: asyncio.Task | None = None


async def _cfg() -> dict[str, Any]:
    store = get_store()

    async def num(key: str, default: int) -> int:
        raw = await store.get_kv(key)
        try:
            return max(1, int(raw)) if raw is not None else default
        except ValueError:
            return default

    return {
        "enabled": (await store.get_kv("groupfull_enabled")) != "false",
        "auto_kick": (await store.get_kv("groupfull_auto_kick")) == "true",
        "count": min(await num("groupfull_kick_count", 100), MAX_KICK_COUNT),
        "activity_days": await num("groupfull_activity_days", 30),
        "protect_days": await num("groupfull_protect_days", 7),
    }


def select_candidates(
    members: list[dict],
    activity: dict[int, int],
    *,
    count: int,
    protect_days: int,
    exempt_ids: set[int],
    active_days: int = 30,
    now: float | None = None,
) -> list[dict]:
    """按活跃度由低到高挑出待清理成员（纯函数，便于测试）。"""
    now = now or time.time()
    protect_after = now - protect_days * 86400
    active_after = now - active_days * 86400
    pool = []
    for m in members:
        uid = int(m["user_id"])
        if uid in exempt_ids or m.get("role") in ("owner", "admin"):
            continue
        if int(m.get("join_time") or 0) > protect_after:
            continue  # 新人保护期
        last_sent = int(m.get("last_sent_time") or 0)
        try:
            level = int(m.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        pool.append(
            {
                "user_id": uid,
                "name": m.get("card") or m.get("nickname") or "",
                "msgs": activity.get(uid, 0),
                "level": level,
                "last_sent": last_sent,
                "recent": last_sent > active_after or activity.get(uid, 0) > 0,
                "join_time": int(m.get("join_time") or 0),
            }
        )
    pool.sort(
        key=lambda x: (
            x["recent"], x["msgs"], x["level"], x["last_sent"], x["join_time"], x["user_id"]
        )
    )
    return pool[:count]


def _superusers() -> set[int]:
    return {int(u) for u in get_driver().config.superusers}


async def _notify_super(bot: Bot, text: str) -> None:
    for uid in _superusers():
        try:
            await bot.call_api("send_private_msg", user_id=uid, message=text)
        except Exception:
            logger.exception(f"群满员通知超管 {uid} 失败")


async def _plan(bot: Bot, group_id: int, cfg: dict[str, Any]) -> tuple[list[dict], int]:
    """返回 (待清理名单, 群成员总数)。"""
    members = await bot.call_api("get_group_member_list", group_id=group_id, no_cache=True)
    since = (datetime.now() - timedelta(days=cfg["activity_days"])).strftime("%Y-%m-%d")
    activity = await get_store().get_activity_since(group_id, since)
    exempt = _superusers() | {int(bot.self_id)}
    picked = select_candidates(
        members, activity, count=cfg["count"], protect_days=cfg["protect_days"],
        exempt_ids=exempt, active_days=cfg["activity_days"],
    )
    return picked, len(members)


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d") if ts else "无记录"


def _preview(group_id: int, picked: list[dict], total: int, cfg: dict[str, Any]) -> str:
    lines = [
        f"群 {group_id} 共 {total} 人，按近 {cfg['activity_days']} 天活跃度倒排，"
        f"待清理 {len(picked)} 人（最不活跃）：",
    ]
    for i, m in enumerate(picked[:PREVIEW_LINES], 1):
        lines.append(
            f"{i}. {m['user_id']} {m['name'][:10]} LV{m['level']}"
            f" 最后发言{_fmt_ts(m['last_sent'])} 近期统计{m['msgs']}条"
        )
    if len(picked) > PREVIEW_LINES:
        lines.append(f"……其余 {len(picked) - PREVIEW_LINES} 人略")
    lines.append(
        f"保护：群主/管理员/超管/机器人/入群不满 {cfg['protect_days']} 天。"
        "依据：QQ 自带的最后发言时间与群等级，加机器人统计的近期发言数。"
    )
    return "\n".join(lines)


async def _bot_is_admin(bot: Bot, group_id: int) -> bool:
    try:
        me = await bot.call_api(
            "get_group_member_info", group_id=group_id, user_id=int(bot.self_id), no_cache=True
        )
    except Exception:
        return False
    return me.get("role") in ("owner", "admin")


async def execute_cleanup(bot: Bot, group_id: int, cfg: dict[str, Any]) -> str:
    """执行清理并返回结果摘要。"""
    if group_id in _running:
        return "该群正在清理中，请稍后。"
    if not await _bot_is_admin(bot, group_id):
        return "机器人不是该群管理员，无法踢人。"
    _running.add(group_id)
    try:
        picked, total = await _plan(bot, group_id, cfg)
        ok, failed = 0, 0
        for m in picked:
            try:
                await bot.call_api(
                    "set_group_kick",
                    group_id=group_id,
                    user_id=m["user_id"],
                    reject_add_request=False,
                )
                ok += 1
            except Exception as e:
                failed += 1
                logger.warning(f"清理踢出失败：group={group_id} user={m['user_id']} {e}")
            await asyncio.sleep(KICK_GAP_SECONDS)
        logger.warning(f"群满员清理完成：group={group_id} 成功={ok} 失败={failed} 清理前人数={total}")
        return f"群 {group_id} 清理完成：已踢出 {ok} 人，失败 {failed} 人（清理前 {total} 人）。"
    finally:
        _running.discard(group_id)


async def check_group(bot: Bot, group_id: int) -> None:
    """检测满员；满员则提醒群、通知超管，按配置预览或自动清理。"""
    cfg = await _cfg()
    if not cfg["enabled"]:
        return
    now = time.time()
    if now - _last_check.get(group_id, 0) < 60:
        return
    _last_check[group_id] = now
    try:
        info = await bot.call_api("get_group_info", group_id=group_id, no_cache=True)
    except Exception:
        logger.warning(f"获取群信息失败：group={group_id}", exc_info=True)
        return
    count, limit = int(info.get("member_count") or 0), int(info.get("max_member_count") or 0)
    if not limit or count < limit:
        return
    store = get_store()
    key = f"groupfull_last_remind_{group_id}"
    last = await store.get_kv(key)
    if last and last.isdigit() and now - int(last) < REMIND_COOLDOWN:
        return
    await store.set_kv(key, str(int(now)))

    try:
        await bot.call_api(
            "send_group_msg",
            group_id=group_id,
            message=f"📢 本群成员已满（{count}/{limit}），新朋友暂时无法加入。"
            "管理员将按活跃度清理长期不发言的成员，腾出名额，请大家保持活跃～",
        )
    except Exception:
        logger.exception(f"发送满员提醒失败：group={group_id}")

    if cfg["auto_kick"]:
        result = await execute_cleanup(bot, group_id, cfg)
        await _notify_super(bot, f"🧹 群满员自动清理\n{result}")
        return
    try:
        picked, total = await _plan(bot, group_id, cfg)
    except Exception as e:
        await _notify_super(bot, f"⚠️ 群 {group_id} 已满员（{count}/{limit}），生成清理名单失败：{e}")
        return
    await _notify_super(
        bot,
        f"⚠️ 群 {group_id} 已满员（{count}/{limit}），已在群内提醒。\n"
        f"{_preview(group_id, picked, total, cfg)}\n"
        f"确认清理请发送：/清理群员 {group_id} 确认",
    )


async def _patrol() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        bots = [b for b in get_bots().values() if isinstance(b, Bot)]
        if not bots:
            continue
        for gid in sorted(merged_whitelist() - disabled_groups()):
            try:
                await check_group(bots[0], gid)
            except Exception:
                logger.exception(f"群满员巡检异常：group={gid}")
            await asyncio.sleep(1)


@_driver.on_bot_connect
async def _start_patrol(bot: Bot) -> None:
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_patrol())


increase_notice = on_notice(rule=GROUP_WHITELIST, priority=5, block=False)


@increase_notice.handle()
async def handle_increase(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    await check_group(bot, event.group_id)


cleanup_cmd = on_command("清理群员", aliases={"cleanup"}, rule=SUPERUSER, priority=1, block=True)


@cleanup_cmd.handle()
async def handle_cleanup(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    tokens = args.extract_plain_text().split()
    confirm = any(t in ("确认", "confirm") for t in tokens)
    nums = [t for t in tokens if t.isdigit()]
    group_id = int(nums[0]) if nums else getattr(event, "group_id", None)
    if not group_id:
        await cleanup_cmd.finish("用法：/清理群员 <群号>（预览）；/清理群员 <群号> 确认（执行）")
    cfg = await _cfg()
    if not confirm:
        try:
            picked, total = await _plan(bot, group_id, cfg)
        except Exception as e:
            await cleanup_cmd.finish(f"生成名单失败：{e}")
        await cleanup_cmd.finish(
            f"{_preview(group_id, picked, total, cfg)}\n确认执行请发送：/清理群员 {group_id} 确认"
        )
    await cleanup_cmd.send(f"开始清理群 {group_id}，请稍候……")
    await cleanup_cmd.finish(await execute_cleanup(bot, group_id, cfg))
