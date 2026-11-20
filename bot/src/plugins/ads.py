"""广告招商与群内广告治理。

两块东西：

1. 招商信息（给人看的）
   - `/广告`：任何人都能发，输出招商说明 + 报价 + 联系方式（全部 kv 可配置）
   - 欢迎语占位符 `{ad}`：新人进群欢迎里插入一句招商提示
     「对于要打广告的朋友：如果你认为你的产品有价值，请联系管理员进行广告报价」

2. 群内自动广告治理（白名单群）
   - 纯规则识别，不花 AI 额度：强特征词（代发广告 / 接推广 / 招代理 …）命中即判；
     组合特征词（推广 / 引流 / 返利 …）需要累计分 + 链接或联系方式才判，减少误伤
   - 合作方名单（ad_partners）里的成员直接放行，避免撤回已付费客户的广告
   - 咨询类（「能发广告吗」「推广怎么收费」）不撤回，直接回招商说明 —— 这是转化入口
   - 判定为广告后的动作 ad_action：
       reply（默认，撤回 + 群里 @ 提示走商务合作）
       silent（只撤回）
       log（只通知超管，不动消息）
   - 同群提示冷却 ad_cooldown（默认 600 秒），冷却内只撤回不刷提示
   - 群主 / 管理员 / 超管发的不算广告；指令消息、引用回复（多半是在吐槽）不处理
   - 处理记录写入 punishments（source='ad'，counted=0 不计入敏感词升级处罚），
     面板「违规记录」可查；有记录的人可以走 /申诉
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import GROUP_WHITELIST
from src.store import get_store

# ---------------------------------------------------------------- 默认配置

DEFAULT_AD_TEXT = (
    "「星潮」群广告合作说明\n"
    "本群平时不欢迎自发广告，但接受经过审核的正规推广。\n"
    "如果你的产品或服务确实有价值，欢迎联系管理员谈合作，我们能提供：\n"
    "· 群内单次推文（可带 @全体）\n"
    "· 定时推送位（早报 / 晚报等固定时段）\n"
    "· 长期合作位（按周 / 按月）\n"
    "只接正经产品与正规服务；灰产、赌博、刷单、代充、拉人引流一律不接。"
)

DEFAULT_AD_PRICE = (
    "报价按推送形式、时段和时长确定，大致分三档：\n"
    "· 单次推文：一次一结\n"
    "· 包周：固定时段多次曝光\n"
    "· 包月：长期合作，价格最优\n"
    "具体价格和档期请找管理员沟通，可以先看往期数据再决定。"
)

# 欢迎语里的一句话招商提示（{ad} 占位符展开成这个）
AD_WELCOME_LINE = "对于要打广告的朋友：如果你认为你的产品有价值，请联系管理员进行广告报价！"

DEFAULTS: dict[str, Any] = {
    "ad_enabled": True,
    "ad_action": "reply",          # reply / silent / log
    "ad_notify": True,             # 命中后私聊通知超管
    "ad_cooldown": 600,            # 同群提示冷却（秒）
    "ad_text": DEFAULT_AD_TEXT,
    "ad_price": DEFAULT_AD_PRICE,
    "ad_contact": "",              # 广告联系人，留空则用首个超管
    "ad_partners": "[]",           # 合作方 QQ 列表（JSON 数组）
}

ACTION_LABEL = {"reply": "撤回并提示", "silent": "只撤回", "log": "只记录并通知超管"}
_ACTION_ALIASES = {
    "reply": "reply", "提示": "reply", "回复": "reply", "撤回提示": "reply",
    "silent": "silent", "静默": "silent", "只撤回": "silent",
    "log": "log", "记录": "log", "只记录": "log", "通知": "log",
}

MAX_AD_TEXT = 1000
MAX_AD_PRICE = 1000
MAX_AD_CONTACT = 20

# ---------------------------------------------------------------- 配置读写


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    if raw is None or raw == "":
        return DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw == "true"
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


async def ad_config() -> dict[str, Any]:
    """全部广告配置（含解析后的合作方列表与联系方式）。"""
    cfg = {key: await _kv(key) for key in DEFAULTS}
    cfg["ad_partners"] = parse_partners(cfg["ad_partners"])
    cfg["ad_contact"] = cfg["ad_contact"] or default_contact()
    return cfg


def default_contact() -> str:
    """广告联系人兜底：开发者 QQ（xingchao_developer_id）。

    不要用「超管列表里的第一个」——超管是权限概念，可以有好几个（小号、协管），
    而对外联系人只能有一个。按数字排序取第一个曾经取到过小号（524430556 < 2217021563），
    群里看到的报价联系人就成了错的人。
    """
    return str(get_config().xingchao_developer_id)


def parse_partners(raw: object) -> list[int]:
    """合作方名单：支持 JSON 数组、逗号分隔字符串、列表。"""
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, list):
            items = data
        else:
            items = re.split(r"[,，\s]+", text)
    out: list[int] = []
    for item in items:
        try:
            uid = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if uid > 0 and uid not in out:
            out.append(uid)
    return out


async def save_partners(partners: list[int]) -> None:
    await get_store().set_kv("ad_partners", json.dumps(partners))


async def is_ad_enabled() -> bool:
    return bool(await _kv("ad_enabled"))


async def get_ad_text() -> str:
    return str(await _kv("ad_text"))


async def get_ad_price() -> str:
    return str(await _kv("ad_price"))


async def get_ad_contact() -> str:
    return str(await _kv("ad_contact")) or default_contact()


async def ad_partners() -> list[int]:
    return parse_partners(await _kv("ad_partners"))


def welcome_line(contact: str = "") -> str:
    """欢迎语 {ad}：一句话招商提示（可带联系方式）。"""
    line = AD_WELCOME_LINE
    if contact:
        line += f"（管理员 QQ：{contact}）"
    return line


async def ad_welcome_line() -> str:
    return welcome_line(await get_ad_contact())


async def ad_notice() -> str:
    """`/广告` 的完整招商信息：说明 + 报价 + 联系方式。"""
    text = await get_ad_text()
    price = await get_ad_price()
    contact = await get_ad_contact()
    return (
        f"{text}\n\n"
        f"【报价】\n{price}\n\n"
        f"【联系方式】管理员 QQ：{contact}\n"
        "（直接把需求、预算和投放时间发给管理员即可，不要先往群里发样稿）"
    )


async def ad_contact_line() -> str:
    """群内拦截提示用的一句话。"""
    contact = await get_ad_contact()
    return (
        "群内不接受自发广告。如果你的产品确实有价值，"
        f"请联系管理员 {contact} 了解广告报价～"
    )

# ---------------------------------------------------------------- 广告识别

# 强特征：命中即判广告（这些词几乎没有正常聊天用法）
STRONG_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"代发广告", r"广告代发", r"广告位招租", r"广告位招商", r"接推广", r"推广接单",
        r"招(?:收)?代理", r"诚招代理", r"一手货源", r"低价代充", r"账号批发",
        r"接引流", r"引流推广", r"刷单", r"兼职刷单", r"日赚\d", r"躺赚", r"稳赚不赔",
        r"代充(?:话费|会员|游戏|点券)", r"接单代做", r"推广位",
    )
)

# 组合特征：需要累计分数
COMBO_WORDS = (
    "推广", "引流", "代理", "加盟", "招商", "返利", "首充", "优惠", "折扣", "私聊",
    "加我", "客服", "兼职", "佣金", "带量", "低价", "秒杀", "福利", "免费领",
    "货源", "微信", "vx", "薇信", "威信", "刷单", "日赚", "躺赚", "看主页", "点我头像",
)

# 咨询类：想投广告的人 → 引导，不撤回
CONSULT_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"(?:能|可以|能不能|可不可以|让不让人?)(?:在)?(?:群里|群内|本群|这里)?(?:发|打|投放|做|推)(?:个|条|一下)?(?:广告|推广)",
        r"(?:想|要|准备|打算)(?:在)?(?:群里|群内|本群|这里)?(?:发|打|投放|做|推)(?:个|条|一下)?(?:广告|推广)",
        r"(?:广告|推广|推送)(?:怎么|如何|怎样)(?:收费|报价|投放|发|做|合作)",
        r"(?:广告|推广|推送)(?:的)?(?:报价|价格|价位|多少钱|费用|收费标准)",
        r"(?:广告|推广)(?:费|费用|位|位费).{0,6}(?:多少|几|怎么|啥|贵不贵)",
        r"冠名|赞助(?:群|活动)?|商务合作|广告合作|合作(?:报价|费用|怎么收费)",
    )
)

LINK_RE = re.compile(r"https?://|www\.|\.(?:com|cn|net|top|xyz|vip|cc|io)\b", re.I)
CONTACT_RE = re.compile(
    r"(?:加|私|联系|敲|找)\s*(?:我|他|她)?\s*(?:微|vx|wx|v信|威信|薇信|企鹅|q)",
    re.I,
)
QQNUM_RE = re.compile(r"(?<!\d)\d{6,12}(?!\d)")

# 吐槽/举报语境：命中这些时降级（除非证据非常强）
METATALK_PATTERNS = tuple(
    re.compile(p)
    for p in (r"广告(?:真|太|好)?(?:多|烦|恶心)", r"别(?:再)?(?:发|打)广告", r"举报", r"撤回",
              r"禁言", r"这是(?:什么)?广告", r"又在(?:发|打)广告")
)


def _norm(text: str) -> str:
    """全角转半角，便于关键词匹配。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def classify_ad(text: str, *, replied: bool = False, long_form: bool = False) -> tuple[str, str]:
    """判断一条文本是不是广告。

    返回 (类型, 命中说明)：类型为 consult（想投广告）/ ad（广告）/ none。
    replied=True 表示这是引用回复（多半在吐槽别人），判定更严格。
    long_form=True 表示长消息（正常讨论居多），需要更强的证据。
    """
    raw = _norm(text).strip()
    if len(raw) < 3:
        return "none", ""

    if len(raw) <= 200:
        for pat in CONSULT_PATTERNS:
            hit = pat.search(raw)
            if hit:
                return "consult", hit.group(0)

    score = 0
    hits: list[str] = []
    for pat in STRONG_PATTERNS:
        hit = pat.search(raw)
        if hit:
            score += 3
            hits.append(hit.group(0))
    for word in COMBO_WORDS:
        if word in raw:
            score += 1
            hits.append(word)
            if len(hits) >= 6:
                break

    has_link = bool(LINK_RE.search(raw))
    has_contact = bool(CONTACT_RE.search(raw)) or bool(QQNUM_RE.search(raw))
    if has_link:
        score += 1
        hits.append("链接")
    if has_contact:
        score += 1
        hits.append("联系方式")

    # 吐槽/举报语境先降分，避免"又在发广告，举报了"被当成广告
    if any(p.search(raw) for p in METATALK_PATTERNS):
        score -= 4
    if replied:
        score -= 2
    if long_form and not has_link:
        score -= 2

    threshold = 3
    if raw.rstrip().endswith(("？", "?")) and not has_link:
        threshold = 5  # 问句放宽，普通咨询别被判广告
    if len(raw) > 120:
        threshold = max(threshold, 4)

    if score >= threshold:
        return "ad", "、".join(dict.fromkeys(hits))[:60]
    return "none", ""

# ---------------------------------------------------------------- 指令

ad_cmd = on_command("广告", aliases={"ads"}, priority=1, block=True)
ad_matcher = on_message(rule=GROUP_WHITELIST, priority=4, block=False)

_last_prompt: dict[int, float] = {}


def _reset_state() -> None:
    """测试用：清空提示冷却。"""
    _last_prompt.clear()


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送广告指令回复失败")


async def _notify_superusers(bot: Bot, text: str) -> None:
    from src.plugins.appeals import notify_superusers

    await notify_superusers(bot, text)


def _is_admin(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        return event.sender.role in ("owner", "admin")
    return False


@ad_cmd.handle()
async def handle_ad_cmd(bot: Bot, event: MessageEvent, matcher: Matcher,
                        args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    action, _, rest = raw.partition(" ")
    action = action.strip()
    rest = rest.strip()
    is_super = event.get_user_id() in get_driver().config.superusers

    if action in ("", "查看", "view") and not rest:
        if not await is_ad_enabled():
            await _send(matcher, "广告招商功能当前已关闭。")
            return
        await _send(matcher, await ad_notice())
        return

    if action in ("报价", "价格", "price"):
        sub, _, tail = rest.partition(" ")
        if is_super and sub in ("设置", "set") and tail.strip():
            text = tail.strip()
            if len(text) > MAX_AD_PRICE:
                await _send(matcher, f"报价内容过长（上限 {MAX_AD_PRICE} 字）。")
                return
            await get_store().set_kv("ad_price", text)
            await _send(matcher, f"广告报价已更新：\n{text}")
            return
        await _send(matcher, f"【广告报价】\n{await get_ad_price()}\n\n联系人 QQ：{await get_ad_contact()}")
        return

    if action in ("咨询", "联系", "contact"):
        if is_super and rest:
            contact = rest.lstrip("@").strip()
            if not contact.isdigit():
                await _send(matcher, "用法：/广告 联系 <QQ号>")
                return
            await get_store().set_kv("ad_contact", contact)
            await _send(matcher, f"广告联系人已设为 {contact}。")
            return
        await _send(matcher, f"广告对接请联系管理员 QQ：{await get_ad_contact()}")
        return

    # 以下均为超管操作
    if not is_super:
        await _send(matcher, "用法：/广告、/广告 报价。商务合作请联系管理员。")
        return

    if action in ("开", "on", "关", "off"):
        enable = action in ("开", "on")
        await get_store().set_kv("ad_enabled", "true" if enable else "false")
        await _send(matcher, f"广告招商已{'开启' if enable else '关闭'}。")
        return

    if action in ("设置", "set"):
        text = rest.strip()
        if not text:
            await _send(matcher, "用法：/广告 设置 <招商说明内容>")
            return
        if len(text) > MAX_AD_TEXT:
            await _send(matcher, f"招商说明过长（上限 {MAX_AD_TEXT} 字）。")
            return
        await get_store().set_kv("ad_text", text)
        await _send(matcher, f"招商说明已更新：\n{text}")
        return

    if action in ("合作", "合作方", "partner"):
        sub, _, tail = rest.partition(" ")
        partners = await ad_partners()
        if sub in ("列表", "list", ""):
            if not partners:
                await _send(matcher, "合作方名单为空。用法：/广告 合作 添加 <QQ>")
            else:
                await _send(matcher, "广告合作方（发言不会被当成广告撤回）：\n" + "、".join(str(p) for p in partners))
            return
        if sub in ("添加", "add") and tail.strip():
            uid_text = tail.strip().lstrip("@")
            if not uid_text.isdigit():
                await _send(matcher, "用法：/广告 合作 添加 <QQ号>")
                return
            uid = int(uid_text)
            if uid in partners:
                await _send(matcher, f"{uid} 已在合作方名单里。")
                return
            partners.append(uid)
            await save_partners(partners)
            await _send(matcher, f"已把 {uid} 加入广告合作方名单（共 {len(partners)} 人）。")
            return
        if sub in ("删除", "移除", "del", "remove") and tail.strip():
            uid_text = tail.strip().lstrip("@")
            if not uid_text.isdigit():
                await _send(matcher, "用法：/广告 合作 删除 <QQ号>")
                return
            uid = int(uid_text)
            if uid not in partners:
                await _send(matcher, f"{uid} 不在合作方名单里。")
                return
            partners.remove(uid)
            await save_partners(partners)
            await _send(matcher, f"已把 {uid} 移出广告合作方名单。")
            return
        await _send(matcher, "用法：/广告 合作 列表 | 添加 <QQ> | 删除 <QQ>")
        return

    if action in ("动作", "处理", "action"):
        key = _ACTION_ALIASES.get(rest.strip().lower()) or _ACTION_ALIASES.get(rest.strip())
        if not key:
            current = await _kv("ad_action")
            await _send(
                matcher,
                f"当前动作：{ACTION_LABEL.get(current, current)}\n"
                "用法：/广告 动作 提示 | 静默 | 记录",
            )
            return
        await get_store().set_kv("ad_action", key)
        await _send(matcher, f"广告处理动作已设为：{ACTION_LABEL[key]}。")
        return

    if action in ("冷却", "cooldown"):
        if not rest.strip().isdigit():
            await _send(matcher, f"当前提示冷却：{await _kv('ad_cooldown')} 秒\n用法：/广告 冷却 <秒>")
            return
        seconds = max(0, min(int(rest.strip()), 86400))
        await get_store().set_kv("ad_cooldown", str(seconds))
        await _send(matcher, f"广告提示冷却已设为 {seconds} 秒。")
        return

    if action in ("通知", "notify"):
        arg = rest.strip()
        if not arg:
            current = "开启" if await _kv("ad_notify") else "关闭"
            await _send(matcher, f"广告命中通知超管当前：{current}。用法：/广告 通知 开|关")
            return
        enable = arg in ("开", "on", "1", "true")
        await get_store().set_kv("ad_notify", "true" if enable else "false")
        await _send(matcher, f"广告命中通知超管已{'开启' if enable else '关闭'}。")
        return

    await _send(
        matcher,
        "广告系统用法：\n"
        "/广告　招商说明 + 报价 + 联系方式\n"
        "/广告 报价　只看报价\n"
        "/广告 合作 列表|添加|删除 <QQ>　合作方发言不算广告\n"
        "/广告 设置 <内容>　招商说明\n"
        "/广告 报价 设置 <内容>　报价单\n"
        "/广告 联系 <QQ>　广告对接人\n"
        "/广告 动作 提示|静默|记录　命中广告怎么处理\n"
        "/广告 冷却 <秒>　同群提示冷却\n"
        "/广告 通知 开|关　命中后是否私聊超管\n"
        "/广告 开|关　招商功能总开关",
    )


@ad_matcher.handle()
async def handle_group_ad(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    if event.user_id == int(bot.self_id):
        return
    # 每条群消息都会走到这里，先做一次便宜的开关判断，再按需读其余配置
    if not await is_ad_enabled():
        return
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return
    if _is_admin(event) or str(event.user_id) in get_driver().config.superusers:
        return  # 群主 / 管理员 / 超管发的不算广告
    if event.user_id in await ad_partners():
        return  # 已备案的合作方放行

    kind, reason = classify_ad(
        text,
        replied=event.reply is not None,
        long_form=len(text) > 300,
    )
    if kind == "none":
        return

    if kind == "consult":
        # 想投广告的人：不撤回，直接给招商信息（这是转化入口）
        try:
            await matcher.send(MessageSegment.at(event.user_id) + "\n" + await ad_notice())
        except MatcherException:
            raise
        except Exception:
            logger.exception("发送广告招商信息失败")
        return

    action = await _kv("ad_action")
    cooldown = int(await _kv("ad_cooldown"))
    notify = bool(await _kv("ad_notify"))
    recalled = False
    if action in ("reply", "silent"):
        try:
            await bot.call_api("delete_msg", message_id=event.message_id)
            recalled = True
        except Exception:
            logger.warning(f"广告消息撤回失败（可能不是管理员）：group={event.group_id}")

    prompted = False
    if action == "reply":
        now = time.time()
        last = _last_prompt.get(event.group_id, 0.0)
        if now - last >= max(0, cooldown):
            _last_prompt[event.group_id] = now
            try:
                await matcher.send(MessageSegment.at(event.user_id) + " " + await ad_contact_line())
                prompted = True
            except MatcherException:
                raise
            except Exception:
                logger.exception("发送广告提示失败")

    try:
        await get_store().add_punishment(
            group_id=event.group_id,
            user_id=event.user_id,
            word=f"广告:{reason}"[:60],
            action="recall" if recalled else "log",
            mute_minutes=0,
            reason="群内自发广告（可走商务合作）",
            source="ad",
            counted=False,
            message_id=event.message_id,
        )
    except Exception:
        logger.exception("记录广告命中失败")

    if notify:
        await _notify_superusers(
            bot,
            "【广告命中】\n"
            f"群 {event.group_id}\n"
            f"发送者：{event.sender.card or event.get_user_id()}（{event.user_id}）\n"
            f"命中：{reason}\n"
            f"处理：{ACTION_LABEL.get(action, action)}"
            f"{'（提示已发）' if prompted else ''}\n"
            f"原文：{text[:200]}",
        )
