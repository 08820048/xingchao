"""AI 工具：读取插件和账号侧的数据：云签到的积分/背包/排行榜，以及机器人自己的 QQ 账号资料。

这些数据在云签到服务器上，不在本地数据库里，AI 要查只能按插件的接口代问一遍。
只开放只读的查询；签到、挖矿、转账、打劫这类会改变积分的玩法仍由用户自己发指令，不开放给 AI。
"""

from __future__ import annotations

import re

from nonebot.log import logger

from src.aitools.registry import tool

# AI 可代查的指令（都只读）
READONLY_QUERIES = ("积分", "签到状态", "排行榜", "我的背包", "我的鱼篓")
_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")


def _client():
    """云签到插件自带的接口客户端；没装或没配 app_key 时返回 None。"""
    try:
        from nonebot import get_plugin_config
        from nonebot_plugin_cloudsignx.client import CloudSignClient
        from nonebot_plugin_cloudsignx.config import Config
    except ImportError:
        return None
    cfg = get_plugin_config(Config)
    return CloudSignClient(str(cfg.cloudsign_app_key), str(cfg.cloudsign_app_secret))


def clean_reply(text: str) -> str:
    """云端返回里可能夹 CQ 码（图片等）：去掉后只留文字；没有文字时返回空串。"""
    return _CQ_RE.sub("", text or "").strip()


@tool(
    "query_points",
    "查询云签到系统里的数据：积分、签到状态、排行榜、背包、鱼篓（用户问“我有多少积分/今天签到了吗/积分排行/背包里有什么”时使用；"
    "只读，不会签到或改动积分；不传 target_qq 时查提问的人自己）。结果以工具返回为准，不要编造数字",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "enum": list(READONLY_QUERIES), "description": "要查的内容"},
            "target_qq": {"type": "integer", "description": "查别人时的 QQ 号，默认查提问者自己"},
        },
        "required": ["query"],
    },
    "all",
)
async def _t_query_points(bot, event, args) -> str:
    group_id = getattr(event, "group_id", None)
    if not group_id:
        return "错误：积分是按群记的，请在群里查询。"
    query = str(args.get("query", "")).strip()
    if query not in READONLY_QUERIES:
        return f"错误：只支持查询 {'/'.join(READONLY_QUERIES)}。"
    client = _client()
    if client is None:
        return "云签到插件没有启用，查不到积分。"
    target = int(args.get("target_qq") or 0)
    try:
        raw = await client.command_request(
            query, qq=event.user_id, qun=int(group_id), botqq=int(bot.self_id), at=target or event.user_id,
            username=getattr(getattr(event, "sender", None), "nickname", "") or "",
        )
    except Exception as e:
        logger.warning(f"云签到查询失败：{type(e).__name__}: {e}")
        return "云签到服务器暂时连不上，稍后再试。"
    if raw.startswith("[PRIVATE:"):
        return "这项结果需要私聊查看，请让对方直接发指令。"
    text = clean_reply(raw)
    if not text:
        return "云签到没有返回文字结果（可能是图片），请让对方直接发指令查看。"
    return text[:1500]


@tool(
    "get_self_info",
    "查看你自己这个 QQ 账号的资料：会员（SVIP/年费/VIP 等级）、QQ 等级、注册年份、昵称、签名"
    "（用户问“你是会员吗/你开会员了吗/你的QQ等级/你多少级”时使用；只说工具返回的内容，不要编造到期时间）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_self_info(bot, event, args) -> str:
    from src.plugins import selfinfo

    if not selfinfo.current():
        await selfinfo.refresh(bot)
    return selfinfo.describe()
