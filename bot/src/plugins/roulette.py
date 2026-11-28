"""轮盘积分局：/轮盘 开局，群友 /上车，/开局 开始，轮到的玩家 /开枪。

设计目标（相对旧第三方 revolver）：
- 状态按群隔离：每群一个 Game，子弹/轮次不再跨群串台；大厅/回合都有超时，不会永久卡住
- 只走白名单群（GROUP_WHITELIST），并由 whitelist.py 的全局闸门挡住第三方插件
- 不再禁言：中枪只结算本群积分，流水落库（roulette_games），可查、可复盘
- 公平赔率：每局 N 人，任何一人的中枪概率都是 1/N（开局时随机生成 6 个枪位，
  命中位在 1..6 均匀分布）。因此庄家中枪按「1 赔 N-1」赔付，而不是按 5N 收/付——
  后者会让庄家每局期望 +13.75 分，随便找个人配合就能刷分。

规则（默认底注 5、组织费 1）：
- 庄家中枪：庄家给其余每位玩家 5 积分（共 5×(N-1)）
- 闲家中枪：该玩家给庄家 5+1=6 积分（底注 + 组织费）
- 所有变动之和为 0，不凭空印分；新玩家首次开户 100，破产可每天领一次补助

指令：
- /轮盘                  开局（自己是庄家）/ 查看当前局
- /上车                  加入大厅
- /开局                  庄家或超管提前开始（大厅超时也会自动开始）
- /开枪                  轮到自己时开枪（超时自动开枪）
- /轮盘 规则|状态|中止|配置|设置 <键> <值>

积分查询/签到/排行/补助在 points.py（/签到、/积分）；本文件只负责轮盘局本身。
AI 工具：start_roulette / begin_roulette / join_roulette / shoot_roulette /
cancel_roulette / get_roulette_status / get_roulette_rules / set_roulette_config
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.background import spawn
from src.config import superuser_ids
from src.permission import GROUP_WHITELIST
from src.store import get_store

# ---------------------------------------------------------------- 配置

DEFAULTS: dict[str, int] = {
    "stake": 5,          # 底注：庄家中枪时给每个人的积分
    "bonus": 1,          # 组织费：闲家中枪时额外付给庄家的积分
    "min_players": 2,
    "max_players": 6,    # 左轮 6 个弹巢，最多 6 人
    "lobby_seconds": 60,
    "turn_seconds": 45,
    "cooldown_seconds": 15,   # 一局结束后的开局冷却，防连续刷局
}

LIMITS: dict[str, tuple[int, int]] = {
    "stake": (1, 1000),
    "bonus": (0, 1000),
    "min_players": (2, 6),
    "max_players": (2, 6),
    "lobby_seconds": (10, 600),
    "turn_seconds": (10, 300),
    "cooldown_seconds": (0, 600),
}

DISPLAY: dict[str, str] = {
    "stake": "底注",
    "bonus": "组织费",
    "min_players": "最少人数",
    "max_players": "最多人数",
    "lobby_seconds": "大厅等待秒数",
    "turn_seconds": "回合超时秒数",
    "cooldown_seconds": "开局冷却秒数",
}

ALIASES: dict[str, str] = {
    "底注": "stake", "押注": "stake", "stake": "stake",
    "组织费": "bonus", "bonus": "bonus",
    "最少": "min_players", "最少人数": "min_players", "min": "min_players",
    "最多": "max_players", "最多人数": "max_players", "max": "max_players",
    "等待": "lobby_seconds", "lobby": "lobby_seconds",
    "回合": "turn_seconds", "turn": "turn_seconds",
    "冷却": "cooldown_seconds", "cooldown": "cooldown_seconds",
}


async def _initial_points() -> int:
    """开户赠送额由 points 插件统一管理，避免两套配置各说各话。"""
    from src.plugins import points

    return (await points.load_settings())["initial"]


async def load_settings() -> dict[str, int]:
    """从 kv 读取配置，越界自动夹紧；每次开局/上车读一次，改配置即时生效。"""
    store = get_store()
    values: dict[str, int] = {}
    for key, default in DEFAULTS.items():
        raw = await store.get_kv(f"roulette_{key}")
        value = default
        if raw:
            try:
                value = int(raw)
            except ValueError:
                logger.warning(f"轮盘配置 roulette_{key} 不是整数，忽略：{raw!r}")
        low, high = LIMITS[key]
        values[key] = max(low, min(high, value))
    if values["min_players"] > values["max_players"]:
        values["min_players"] = values["max_players"]
    return values


async def update_setting(key: str, raw_value: str) -> tuple[bool, str]:
    """超管改配置；值会被夹到安全范围，人数上下限互相校验。"""
    key = ALIASES.get(key.strip(), key.strip())
    if key not in DEFAULTS:
        return False, "可配置项：" + "、".join(f"{DISPLAY[k]}({k})" for k in DEFAULTS)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return False, "值必须是整数"
    low, high = LIMITS[key]
    value = max(low, min(high, value))
    current = await load_settings()
    if key == "min_players" and value > current["max_players"]:
        return False, f"最少人数不能大于最多人数（当前 {current['max_players']}）"
    if key == "max_players" and value < current["min_players"]:
        return False, f"最多人数不能小于最少人数（当前 {current['min_players']}）"
    await get_store().set_kv(f"roulette_{key}", str(value))
    return True, f"{DISPLAY[key]}已设为 {value}"


# ---------------------------------------------------------------- 游戏状态

@dataclass
class Game:
    group_id: int
    creator: int
    players: list[int]
    stake: int
    bonus: int
    min_players: int
    max_players: int
    lobby_seconds: int
    turn_seconds: int
    phase: str = "lobby"                      # lobby / playing
    slots: list[int] = field(default_factory=list)   # 6 个枪位（可能重复）
    hit_index: int = 0                        # 0-based，子弹所在枪位
    turn: int = 0                             # 当前第几枪（0-based）
    turn_token: int = 0                       # 防止过期的超时任务误开枪
    created_at: float = field(default_factory=time.time)


_games: dict[int, Game] = {}
_last_end: dict[int, float] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock(group_id: int) -> asyncio.Lock:
    return _locks.setdefault(group_id, asyncio.Lock())


def _drop_game(group_id: int) -> None:
    """结束/取消一局并记下冷却起点。"""
    _games.pop(group_id, None)
    _last_end[group_id] = time.time()


def _at(user_id: int) -> MessageSegment:
    return MessageSegment.at(int(user_id))


def _text(text: str) -> Message:
    return Message([MessageSegment.text(text)])


def build_slots(players: list[int], rng=random) -> tuple[list[int], int]:
    """生成 6 个枪位与命中位。

    hit_index 在 0..5 均匀，slots[hit_index] 因此对每个玩家都是 1/N 概率——
    这正是「庄家 1 赔 N-1」赔率公平的依据。
    """
    slots = [rng.choice(players) for _ in range(6)]
    return slots, rng.randrange(6)


def compute_deltas(
    creator: int, victim: int, players: list[int], stake: int, bonus: int
) -> dict[int, int]:
    """纯结算：返回每个玩家的积分变化，所有值之和恒为 0。

    - 庄家中枪：庄家 -stake×(N-1)，其余每人 +stake
    - 闲家中枪：中枪者 -(stake+bonus)，庄家 +(stake+bonus)
    """
    deltas = {user_id: 0 for user_id in players}
    if victim == creator:
        for user_id in players:
            if user_id != creator:
                deltas[user_id] += stake
        deltas[creator] -= stake * (len(players) - 1)
    else:
        deltas[victim] -= stake + bonus
        deltas[creator] += stake + bonus
    return {user_id: delta for user_id, delta in deltas.items() if delta}


# ---------------------------------------------------------------- 展示

async def status_message(group_id: int) -> Message:
    game = _games.get(group_id)
    if game is None:
        return _text("本群没有进行中的轮盘局。发 /轮盘 开一局，/积分 看自己的积分。")
    if game.phase == "lobby":
        joined = "、".join(f"{uid}" for uid in game.players)
        msg = _text(
            f"🎲 轮盘局等待中（{len(game.players)}/{game.max_players} 人）\n"
            f"已上车：{joined}\n"
            f"/上车 加入 · /开局 开始 · /轮盘 中止 取消\n"
            f"{game.lobby_seconds} 秒后自动开始"
        )
        return msg
    shooter = game.slots[game.turn]
    msg = _text(f"🎲 轮盘进行中：第 {game.turn + 1}/6 枪，轮到 ")
    msg += _at(shooter)
    msg += f"，请发 /开枪（{game.turn_seconds} 秒后自动）"
    return msg


async def rules_message() -> Message:
    settings = await load_settings()
    stake, bonus = settings["stake"], settings["bonus"]
    return _text(
        "🎲 轮盘规则\n"
        "1. /轮盘 开局（你是庄家），群友 /上车，/开局 或大厅倒计时结束后开始。\n"
        "2. 每局 2–6 人、6 个枪位；每枪随机点名一位玩家，/开枪；超时自动开枪。\n"
        "3. 中枪即结束本局。\n"
        "4. 结算（零和，不凭空印分）：\n"
        f"   · 庄家中枪：给其余每位玩家 {stake} 积分（共 {stake}×(N-1)）。\n"
        f"   · 闲家中枪：付给庄家 {stake + bonus} 积分（底注 {stake} + 组织费 {bonus}）。\n"
        "5. 每局 N 人中枪概率都是 1/N；庄家也必须上车，不能只收钱。\n"
        "6. /积分 看余额，/积分 领取 每日破产补助，/积分 排行 看榜。"
    )


async def config_message() -> Message:
    settings = await load_settings()
    msg = _text("⚙️ 轮盘配置（超管）\n")
    for key, value in settings.items():
        low, high = LIMITS[key]
        msg += f"{DISPLAY[key]}（{key}）：{value}（{low}–{high}）\n"
    msg += "修改：/轮盘 设置 <键> <值>，如 /轮盘 设置 底注 10"
    return msg


# ---------------------------------------------------------------- 大厅 / 开局

async def start_lobby(bot: Bot, group_id: int, creator: int) -> tuple[bool, Message]:
    """开一个等待大厅；已有局时返回当前状态而不是覆盖。"""
    async with _lock(group_id):
        if group_id in _games:
            return False, await status_message(group_id)
        settings = await load_settings()
        if creator not in superuser_ids():
            last = _last_end.get(group_id)
            if last is not None and time.time() - last < settings["cooldown_seconds"]:
                wait = int(settings["cooldown_seconds"] - (time.time() - last)) + 1
                return False, _text(f"刚结束一局，歇 {wait} 秒再开吧。")
        balance = await get_store().ensure_points(
            group_id, creator, await _initial_points()
        )
        need = settings["stake"] * (settings["min_players"] - 1)
        if balance < need:
            return False, _text(
                f"你的积分不够开庄（至少 {settings['min_players']} 人、需要 {need}，"
                f"当前 {balance}）。发 /积分 领取 可以领每日补助。"
            )
        game = Game(
            group_id=group_id,
            creator=creator,
            players=[creator],
            stake=settings["stake"],
            bonus=settings["bonus"],
            min_players=settings["min_players"],
            max_players=settings["max_players"],
            lobby_seconds=settings["lobby_seconds"],
            turn_seconds=settings["turn_seconds"],
        )
        _games[group_id] = game
        spawn(_lobby_timeout(bot, game), f"roulette-lobby-{group_id}")
        msg = _text("🎲 ")
        msg += _at(creator)
        msg += (
            f" 开了个轮盘局（底注 {game.stake}，{game.min_players}–{game.max_players} 人）。\n"
            "/上车 加入 · /开局 开始 · /轮盘 中止 取消\n"
            f"{game.lobby_seconds} 秒后自动开始；/轮盘 规则 看玩法"
        )
        return True, msg


async def join_lobby(bot: Bot, group_id: int, user_id: int) -> tuple[bool, Message]:
    async with _lock(group_id):
        game = _games.get(group_id)
        if game is None:
            return False, _text("本群没有等待中的轮盘局，先发 /轮盘 开一局。")
        if game.phase != "lobby":
            return False, _text("这局已经开始了，等下一局吧。")
        if user_id in game.players:
            return False, _text("你已经在车上了。")
        if len(game.players) >= game.max_players:
            return False, _text(f"车满了（最多 {game.max_players} 人），等下一局吧。")
        balance = await get_store().ensure_points(
            group_id, user_id, await _initial_points()
        )
        need = game.stake + game.bonus
        if balance < need:
            return False, _text(
                f"积分不够：这局中枪要付 {need}，你现在只有 {balance}。"
                "发 /积分 领取 可以领每日补助。"
            )
        game.players.append(user_id)
        msg = _text("✅ ")
        msg += _at(user_id)
        msg += f" 上车（{len(game.players)}/{game.max_players}）"
        if len(game.players) >= game.min_players:
            msg += "。人够了，/开局 可以开始"
        return True, msg


async def _lobby_timeout(bot: Bot, game: Game) -> None:
    await asyncio.sleep(game.lobby_seconds)
    async with _lock(game.group_id):
        if _games.get(game.group_id) is not game or game.phase != "lobby":
            return
        if len(game.players) < game.min_players:
            _drop_game(game.group_id)
            await _send(bot, game.group_id, _text("⏰ 时间到，上车人数不够，局散了。"))
            return
        ok, msg = await _begin_locked(bot, game)
        if ok:
            msg = _text("⏰ 大厅时间到，自动开局。\n") + msg
        await _send(bot, game.group_id, msg)


async def _begin_locked(bot: Bot, game: Game) -> tuple[bool, Message]:
    """在已持有群锁的前提下开始游戏。"""
    if game.phase != "lobby":
        return False, _text("这局已经开始了。")
    if len(game.players) < game.min_players:
        return False, _text(f"人数不够：至少 {game.min_players} 人才能开局。")
    store = get_store()
    creator_balance = await store.get_points(game.group_id, game.creator) or 0
    need = game.stake * (len(game.players) - 1)
    if creator_balance < need:
        _drop_game(game.group_id)
        return False, _text(
            f"庄家积分不够：自己中枪要赔 {need}，当前只有 {creator_balance}，局散了。"
            "发 /积分 领取 可以领每日补助。"
        )
    game.phase = "playing"
    game.slots, game.hit_index = build_slots(game.players)
    game.turn = 0
    game.turn_token += 1
    spawn(_turn_timeout(bot, game, game.turn_token), f"roulette-turn-{game.group_id}")
    msg = _text("🎲 开局！")
    msg += _at(game.creator)
    msg += (
        f" 当庄，{len(game.players)} 人、底注 {game.stake}。\n"
        f"第 1/6 枪："
    )
    msg += _at(game.slots[0])
    msg += f"，请发 /开枪（{game.turn_seconds} 秒后自动）"
    return True, msg


async def begin_game(bot: Bot, group_id: int, user_id: int) -> tuple[bool, Message]:
    async with _lock(group_id):
        game = _games.get(group_id)
        if game is None:
            return False, _text("本群没有等待中的轮盘局，先发 /轮盘 开一局。")
        if game.phase != "lobby":
            return False, _text("这局已经开始了。")
        if user_id != game.creator and user_id not in superuser_ids():
            return False, _text("只有庄家或超管能提前开局。")
        return await _begin_locked(bot, game)


async def cancel_lobby(bot: Bot, group_id: int, user_id: int) -> tuple[bool, Message]:
    async with _lock(group_id):
        game = _games.get(group_id)
        if game is None:
            return False, _text("本群没有进行中的轮盘局。")
        is_super = user_id in superuser_ids()
        if user_id != game.creator and not is_super:
            return False, _text("只有庄家或超管能中止。")
        if game.phase == "playing" and not is_super:
            return False, _text("对局已经开始，庄家不能中止；超管可以强制中止。")
        _drop_game(group_id)
        return True, _text("轮盘局已取消，积分没有变动。")


# ---------------------------------------------------------------- 开枪 / 结算

async def _turn_timeout(bot: Bot, game: Game, token: int) -> None:
    await asyncio.sleep(game.turn_seconds)
    async with _lock(game.group_id):
        if (
            _games.get(game.group_id) is not game
            or game.phase != "playing"
            or game.turn_token != token
        ):
            return
        shooter = game.slots[game.turn]
        await _send(bot, game.group_id, _text("⏰ ") + _at(shooter) + " 超时，自动开枪。")
        try:
            _, msg = await _resolve_shot(bot, game, auto=True)
        except Exception:
            logger.exception(f"轮盘自动开枪失败：group={game.group_id}")
            _drop_game(game.group_id)
            await _send(bot, game.group_id, _text("自动开枪出错了，本局已取消，积分没有变动。"))
            return
        await _send(bot, game.group_id, msg)


async def shoot(bot: Bot, group_id: int, user_id: int) -> tuple[bool, Message]:
    async with _lock(group_id):
        game = _games.get(group_id)
        if game is None:
            return False, _text("本群没有进行中的轮盘局，先发 /轮盘 开一局。")
        if game.phase != "playing":
            return False, _text("还在等人上车，发 /开局 开始后再开枪。")
        shooter = game.slots[game.turn]
        if user_id != shooter:
            return False, _text("现在轮到 ") + _at(shooter) + "，还没到你。"
        return await _resolve_shot(bot, game, auto=False)


async def _resolve_shot(bot: Bot, game: Game, auto: bool) -> tuple[bool, Message]:
    """处理当前枪位；命中则结算并结束本局。调用方必须持有群锁。"""
    index = game.turn
    shooter = game.slots[index]
    if index != game.hit_index:
        game.turn += 1
        game.turn_token += 1
        next_shooter = game.slots[game.turn]
        spawn(_turn_timeout(bot, game, game.turn_token), f"roulette-turn-{game.group_id}")
        msg = _text("💨 咔！") + _at(shooter) + f" 安全。\n第 {game.turn + 1}/6 枪："
        msg += _at(next_shooter)
        msg += f"，请发 /开枪（{game.turn_seconds} 秒后自动）"
        return True, msg

    # 命中：先算账，再落库，成功后才把局从内存移除
    players = list(game.players)
    deltas = compute_deltas(game.creator, shooter, players, game.stake, game.bonus)
    try:
        balances = await get_store().settle_roulette(
            group_id=game.group_id,
            creator_id=game.creator,
            victim_id=shooter,
            player_count=len(players),
            stake=game.stake,
            bonus=game.bonus,
            deltas=deltas,
        )
    except Exception:
        logger.exception(f"轮盘结算失败：group={game.group_id}")
        _drop_game(game.group_id)
        return False, _text("结算失败，本局已取消，积分没有变动。请联系超管看日志。")

    _drop_game(game.group_id)
    prefix = "⏰ 超时自动开枪。\n" if auto else ""
    msg = _text(prefix + "💥 砰！")
    msg += _at(shooter)
    msg += f" 中枪！子弹在第 {index + 1}/6 枪。\n"
    if shooter == game.creator:
        msg += (
            f"庄家中枪：给其余 {len(players) - 1} 人各 {game.stake} 积分"
            f"（共 -{game.stake * (len(players) - 1)}）。\n"
        )
    else:
        msg += (
            f"闲家中枪：付给庄家 {game.stake + game.bonus} 积分"
            f"（底注 {game.stake} + 组织费 {game.bonus}）。\n"
        )
    for user_id, delta in sorted(deltas.items(), key=lambda item: -abs(item[1])):
        sign = "+" if delta > 0 else ""
        msg += f"{user_id}：{sign}{delta} → {balances.get(user_id, 0)}\n"
    msg += "局散了，/轮盘 再来一局。"
    return True, msg


async def _send(bot: Bot, group_id: int, message: Message) -> None:
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=message)
    except Exception:
        logger.exception(f"轮盘消息发送失败：group={group_id}")


# ---------------------------------------------------------------- 指令

roulette_cmd = on_command(
    "轮盘", aliases={"左轮", "转盘", "装弹"}, rule=GROUP_WHITELIST, priority=5, block=True
)
join_cmd = on_command(
    "上车", aliases={"加入轮盘", "参加轮盘"}, rule=GROUP_WHITELIST, priority=5, block=True
)
start_cmd = on_command(
    "开局", aliases={"开始轮盘"}, rule=GROUP_WHITELIST, priority=5, block=True
)
shoot_cmd = on_command("开枪", rule=GROUP_WHITELIST, priority=5, block=True)
@roulette_cmd.handle()
async def handle_roulette(
    matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    raw = args.extract_plain_text().strip()
    if not raw:
        _, msg = await start_lobby(bot, event.group_id, event.user_id)
        await matcher.send(msg)
        return
    parts = raw.split()
    head = parts[0]
    if head in ("规则", "rule", "rules"):
        await matcher.send(await rules_message())
        return
    if head in ("状态", "status"):
        await matcher.send(await status_message(event.group_id))
        return
    if head in ("开始", "开局", "start"):
        _, msg = await begin_game(bot, event.group_id, event.user_id)
        await matcher.send(msg)
        return
    if head in ("中止", "取消", "cancel", "stop"):
        _, msg = await cancel_lobby(bot, event.group_id, event.user_id)
        await matcher.send(msg)
        return
    if head in ("配置", "config", "设置", "set"):
        if event.user_id not in superuser_ids():
            await matcher.finish("轮盘配置只有超管能看/改。")
        if head in ("设置", "set"):
            if len(parts) < 3:
                await matcher.finish("用法：/轮盘 设置 <键> <值>，如 /轮盘 设置 底注 10")
            _, text = await update_setting(parts[1], parts[2])
            await matcher.send(text)
        else:
            await matcher.send(await config_message())
        return
    await matcher.finish("用法：/轮盘 开一局 | /轮盘 规则 | /轮盘 状态 | /轮盘 中止 | /轮盘 配置")


@join_cmd.handle()
async def handle_join(matcher: Matcher, bot: Bot, event: GroupMessageEvent) -> None:
    _, msg = await join_lobby(bot, event.group_id, event.user_id)
    await matcher.send(msg)


@start_cmd.handle()
async def handle_start(matcher: Matcher, bot: Bot, event: GroupMessageEvent) -> None:
    _, msg = await begin_game(bot, event.group_id, event.user_id)
    await matcher.send(msg)


@shoot_cmd.handle()
async def handle_shoot(matcher: Matcher, bot: Bot, event: GroupMessageEvent) -> None:
    _, msg = await shoot(bot, event.group_id, event.user_id)
    await matcher.send(msg)
