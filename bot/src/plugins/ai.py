"""AI 问答：@机器人 / 昵称唤起时调用 LLM（OpenAI 兼容接口，如 B.AI）回答。

- 触发：to_me 且非指令的群消息（超管私聊对话也可用）
- 上下文：每群保留最近 N 轮会话（内存），/ai clear 可清空
- 护栏：每群 / 每人每日调用上限（面板可配），超限静默忽略
- 配置：base_url / api_key 来自环境变量；开关、模型、系统提示词、限额存 SQLite kv
  （面板「AI」页可配置）。base_url 或 api_key 缺失时功能自动禁用并打日志。
- 非流式调用，回复超长截断（QQ 消息限长）
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import SUPERUSER
from src.store import get_store

# ---------------------------------------------------------------- 配置（kv）

_KV_KEYS = ("ai_enabled", "ai_model", "ai_system_prompt", "ai_ctx_rounds", "ai_limit_group", "ai_limit_user")

DEFAULTS: dict[str, Any] = {
    "ai_enabled": True,
    "ai_model": "deepseek-v4-flash",
    "ai_system_prompt": (
        "你是「星潮」，一个开源的 QQ 群助手机器人（官网 https://xingchao.dev）。"
        "回答简洁、友好、口语化，避免长篇大论；不懂就说不懂，不要编造。"
        "你可以调用工具查询群信息、成员列表、活跃统计、当前时间日期，进行算术计算，"
        "以及执行群管理、获取 GitHub 趋势榜单等操作；"
        "涉及禁言、踢人、改配置等敏感操作时，先向用户确认再执行。"
    ),
    "ai_ctx_rounds": 5,
    "ai_limit_group": 100,
    "ai_limit_user": 20,
}

_client: Any = None
_client_sig: tuple[str, str] | None = None


async def _ai_creds() -> tuple[str, str]:
    """API 地址与密钥：SQLite kv 优先（面板可改），环境变量兜底。"""
    store = get_store()
    base = await store.get_kv("ai_base_url")
    key = await store.get_kv("ai_api_key")
    cfg = get_config()
    return (base or cfg.xingchao_ai_base_url, key or cfg.xingchao_ai_api_key)


async def _kv(key: str) -> Any:
    raw = await get_store().get_kv(key)
    if raw is None:
        raw = DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw != "false"
    if isinstance(default, int):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default
    return raw


async def ai_config() -> dict[str, Any]:
    return {k: await _kv(k) for k in _KV_KEYS}


async def is_configured() -> bool:
    base, key = await _ai_creds()
    return bool(base) and bool(key)


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:5]}****{key[-4:]}"


async def get_client() -> Any | None:
    """按当前凭据返回客户端；凭据变更（面板更新密钥/地址）时自动重建。"""
    global _client, _client_sig
    base, key = await _ai_creds()
    if not base or not key:
        return None
    sig = (base, key)
    if _client is None or _client_sig != sig:
        try:
            from openai import AsyncOpenAI

            _client = AsyncOpenAI(api_key=key, base_url=base)
            _client_sig = sig
            logger.info(f"AI 客户端已就绪：{base}")
        except Exception:
            logger.exception("初始化 OpenAI 客户端失败，AI 功能禁用")
            _client = None
            return None
    return _client


async def is_ai_enabled() -> bool:
    if not (await is_configured()):
        return False
    return await _kv("ai_enabled") is True


# ---------------------------------------------------------------- 上下文

_ctx: dict[int, list[dict[str, str]]] = {}


def _get_history(group_key: int) -> list[dict[str, str]]:
    return _ctx.setdefault(group_key, [])


def _append_history(group_key: int, role: str, content: str, rounds: int) -> None:
    hist = _get_history(group_key)
    hist.append({"role": role, "content": content})
    # 每轮 = user + assistant 两条，保留最近 rounds 轮
    max_len = max(2, rounds * 2)
    while len(hist) > max_len:
        hist.pop(0)


# ---------------------------------------------------------------- 限额

async def _check_quota(event: MessageEvent, group_limit: int, user_limit: int) -> tuple[bool, str]:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    key = f"ai_usage_{day}"
    raw = await store.get_kv(key)
    try:
        usage: dict[str, Any] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    gid = str(getattr(event, "group_id", "private"))
    uid = str(event.user_id)
    groups: dict[str, int] = usage.get("groups", {})
    users: dict[str, int] = usage.get("users", {})
    group_limit, user_limit = int(group_limit), int(user_limit)  # 防御性转换
    if groups.get(gid, 0) >= group_limit:
        return False, "本群今日 AI 次数已用完"
    if users.get(uid, 0) >= user_limit:
        return False, "你今日的 AI 次数已用完"
    groups[gid] = groups.get(gid, 0) + 1
    users[uid] = users.get(uid, 0) + 1
    usage["groups"] = groups
    usage["users"] = users
    await store.set_kv(key, json.dumps(usage, ensure_ascii=False))
    return True, ""


async def _usage_payload() -> dict[str, Any]:
    store = get_store()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = await store.get_kv(f"ai_usage_{day}")
    try:
        usage = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        usage = {}
    return {"day": day, "groups": usage.get("groups", {}), "users": usage.get("users", {})}


# ---------------------------------------------------------------- 核心问答

MAX_REPLY_CHARS = 1500
MAX_TOOL_ROUNDS = 4
MAX_BAN_SECONDS = 30 * 24 * 3600  # OneBot v11 上限 30 天

ROLE_NAME = {"owner": "群主", "admin": "管理员", "member": "普通成员"}


async def _build_scene(bot, event: MessageEvent) -> str:
    """构建提问者与群信息上下文，让 AI 天然知道“我是谁、在哪个群、有什么身份”。"""
    from datetime import datetime

    cfg = get_config()
    lines = [f"提问者 QQ：{event.user_id}"]
    if isinstance(event, GroupMessageEvent):
        try:
            m = await bot.call_api(
                "get_group_member_info",
                group_id=event.group_id, user_id=event.user_id, no_cache=True,
            )
            role = ROLE_NAME.get(m.get("role", ""), m.get("role", "未知"))
            lines.append(f"提问者身份：{role}")
            card = m.get("card") or m.get("nickname")
            if card:
                lines.append(f"群昵称：{card}")
            jt = m.get("join_time")
            if jt:
                lines.append(f"入群时间：{datetime.fromtimestamp(int(jt)).strftime('%Y-%m-%d')}")
        except Exception:
            logger.debug("获取提问者群信息失败", exc_info=True)
        try:
            info = await bot.call_api("get_group_info", group_id=event.group_id)
            if info.get("group_name"):
                lines.append(f"所在群：{info['group_name']}（{event.group_id}，{info.get('member_count')} 人）")
        except Exception:
            pass
    lines.append(
        f"开发者信息：QQ {cfg.xingchao_developer_id}，博客 {cfg.xingchao_developer_blog}，"
        f"项目官网 {cfg.xingchao_developer_site}。"
        "当用户询问你是谁做的/开发者/作者/是谁开发的时候：必须在回复中介绍以上开发者信息"
        "（QQ 号、博客、官网都要提到），"
        f"并且必须在回复中包含占位符 {{dev_at}} 来@开发者（程序会替换为真实的@消息）。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 工具注册表
# 每个工具：schema（OpenAI function 定义）、perm（all=所有人 / superuser=仅超管）、
# handler(bot, event, args) -> str（给 LLM 的结果摘要，中文）


def _tool(name: str, description: str, params: dict, perm: str, handler) -> tuple[dict, Any]:
    """返回 (OpenAI schema, 执行器)。schema 传给 SDK，执行器本地保存。"""
    schema = {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }
    return schema, (perm, handler)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


async def _t_group_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号，且当前不是群聊环境。"
    info = await bot.call_api("get_group_info", group_id=int(group_id))
    return _j({"群号": info["group_id"], "群名": info.get("group_name"), "成员数": info.get("member_count"), "上限": info.get("max_member_count")})


async def _t_member_list(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：未指定群号。"
    members = await bot.call_api("get_group_member_list", group_id=int(group_id))
    brief = [
        {"QQ": m["user_id"], "昵称": m.get("nickname"), "名片": m.get("card"), "角色": m.get("role")}
        for m in members[:30]
    ]
    return _j({"总数": len(members), "成员（最多30）": brief})


async def _t_member_info(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    uid = args.get("user_id")
    if not group_id or not uid:
        return "错误：需要群号与用户 QQ。"
    m = await bot.call_api("get_group_member_info", group_id=int(group_id), user_id=int(uid))
    return _j({"QQ": m["user_id"], "昵称": m.get("nickname"), "群名片": m.get("card"), "角色": m.get("role"), "入群时间戳": m.get("join_time"), "等级": m.get("level")})


async def _t_mute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    minutes = int(args.get("duration_minutes", 10))
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=max(60, min(minutes * 60, MAX_BAN_SECONDS)))
    return f"已禁言 {args['user_id']} {minutes} 分钟。"


async def _t_unmute(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_ban", group_id=int(group_id), user_id=int(args["user_id"]), duration=0)
    return f"已解除 {args['user_id']} 的禁言。"


async def _t_kick(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    await bot.call_api("set_group_kick", group_id=int(group_id), user_id=int(args["user_id"]))
    return f"已将 {args['user_id']} 移出群聊。"


async def _t_whole_ban(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    enable = bool(args.get("enable", True))
    await bot.call_api("set_group_whole_ban", group_id=int(group_id), enable=enable)
    return "已开启全体禁言。" if enable else "已关闭全体禁言。"


async def _t_stats(bot, event, args) -> str:
    from src.plugins.ai import _usage_payload  # noqa: F401  （保持模块一致）
    store = get_store()
    day = args.get("day") or datetime.now().astimezone().strftime("%Y-%m-%d")
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if group_id:
        total, users = await store.get_group_day_stat(int(group_id), day)
        top = await store.get_top_users(int(group_id), day)
        return _j({"日期": day, "群号": int(group_id), "消息总数": total, "参与人数": users, "Top5": [{"QQ": u, "条数": c} for u, c in top]})
    overview = await store.get_day_overview(day)
    return _j({"日期": day, "各群": [{"群号": g, "消息数": c} for g, c in overview]})


async def _t_whitelist_list(bot, event, args) -> str:
    from src.permission import merged_whitelist
    env_ids = get_config().xingchao_group_whitelist
    return _j({"白名单": [{"群号": g, "来源": "环境变量" if g in env_ids else "运行时"} for g in sorted(merged_whitelist())]})


async def _t_whitelist_add(bot, event, args) -> str:
    from src.permission import add_runtime_group
    ok = await add_runtime_group(int(args["group_id"]))
    return f"已添加群 {args['group_id']}。" if ok else f"群 {args['group_id']} 已在白名单中。"


async def _t_whitelist_del(bot, event, args) -> str:
    from src.permission import remove_runtime_group
    gid = int(args["group_id"])
    if gid in get_config().xingchao_group_whitelist:
        return f"群 {gid} 来自环境变量，无法移除，请修改 XINGCHAO_GROUP_WHITELIST。"
    ok = await remove_runtime_group(gid)
    return f"已移除群 {gid}。" if ok else f"群 {gid} 不在运行时白名单中。"


async def _t_group_switch(bot, event, args) -> str:
    from src.permission import set_group_enabled
    gid = int(args["group_id"])
    enable = bool(args.get("enabled", True))
    changed = await set_group_enabled(gid, enable)
    state = "开启" if enable else "关闭"
    return f"已{state}群 {gid} 的业务。" if changed else f"群 {gid} 已处于{state}状态。"


async def _t_reply_list(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    items = reply_plugin.get_items()
    return _j({"词条数": len(items), "词条": [{"id": it["id"], "触发词": it["pattern"], "匹配": it.get("match", "exact"), "启用": it.get("enabled", True)} for it in items]})


async def _t_reply_reload(bot, event, args) -> str:
    from src.plugins import reply as reply_plugin
    return f"词库已重载，共 {reply_plugin.reload_replies()} 条词条。"


async def _t_gh_trending(bot, event, args) -> str:
    from src.plugins import github as gh
    since = str(args.get("since", "daily"))
    language = str(args.get("language", ""))
    try:
        items = await gh.fetch_trending(since, language)
        return _j({"趋势榜": items})
    except Exception as e:
        return f"获取趋势失败：{e}"


async def _t_now(bot, event, args) -> str:
    now = datetime.now().astimezone()
    week = "一二三四五六日"[now.weekday()]
    return _j({
        "日期": now.strftime("%Y-%m-%d"),
        "时间": now.strftime("%H:%M:%S"),
        "星期": f"星期{week}",
        "时区": now.tzname() or str(now.utcoffset()),
        "ISO": now.isoformat(timespec="seconds"),
        "Unix时间戳": int(now.timestamp()),
    })


def _safe_calc(expr: str) -> Any:
    """白名单 AST 求值：仅数字与四则运算/幂/取余，杜绝任意代码执行。"""
    import ast as _ast
    import operator as _op

    bin_ops = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
               _ast.Div: _op.truediv, _ast.FloorDiv: _op.floordiv,
               _ast.Mod: _op.mod, _ast.Pow: _op.pow}
    unary_ops = {_ast.UAdd: _op.pos, _ast.USub: _op.neg}

    def ev(node):
        if isinstance(node, _ast.Expression):
            return ev(node.body)
        if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, _ast.BinOp) and type(node.op) in bin_ops:
            return bin_ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in unary_ops:
            return unary_ops[type(node.op)](ev(node.operand))
        raise ValueError("仅支持数字与 + - * / // % ** 运算")

    return ev(_ast.parse(expr, mode="eval"))


async def _t_calc(bot, event, args) -> str:
    expr = str(args.get("expression", ""))[:200]
    if not expr:
        return "错误：缺少 expression。"
    try:
        result = _safe_calc(expr)
        r = int(result) if isinstance(result, float) and result.is_integer() else result
        return _j({"表达式": expr, "结果": r})
    except ZeroDivisionError:
        return "错误：除数为零。"
    except Exception as e:
        return f"错误：{e}"


def _build_tools(is_superuser: bool) -> list[dict]:
    tools = [
        _tool("get_group_info", "获取群聊信息（群名、成员数）",
              {"type": "object", "properties": {"group_id": {"type": "integer", "description": "群号，默认当前群"}}, "required": []},
              "all", _t_group_info),
        _tool("get_member_list", "获取群成员列表（QQ、昵称、群名片、角色）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
              "all", _t_member_list),
        _tool("get_member_info", "获取指定成员的详细信息",
              {"type": "object", "properties": {"user_id": {"type": "integer", "description": "成员 QQ"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "all", _t_member_info),
        _tool("get_active_stats", "查询群活跃统计（消息量、参与人数、发言Top5）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}, "day": {"type": "string", "description": "YYYY-MM-DD，默认今天"}}, "required": []},
              "all", _t_stats),
        _tool("mute_member", "禁言群成员（分钟）",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "duration_minutes": {"type": "integer", "description": "默认10，上限43200"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_mute),
        _tool("unmute_member", "解除成员禁言",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_unmute),
        _tool("kick_member", "将成员移出群聊",
              {"type": "object", "properties": {"user_id": {"type": "integer"}, "group_id": {"type": "integer"}}, "required": ["user_id"]},
              "superuser", _t_kick),
        _tool("set_whole_ban", "开启/关闭全体禁言",
              {"type": "object", "properties": {"enable": {"type": "boolean"}, "group_id": {"type": "integer"}}, "required": ["enable"]},
              "superuser", _t_whole_ban),
        _tool("list_whitelist", "查看群白名单",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_whitelist_list),
        _tool("add_whitelist_group", "添加群到白名单",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
              "superuser", _t_whitelist_add),
        _tool("remove_whitelist_group", "从白名单移除群（env 来源除外）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
              "superuser", _t_whitelist_del),
        _tool("set_group_business", "开启/关闭某群的业务（临时开关）",
              {"type": "object", "properties": {"group_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["group_id", "enabled"]},
              "superuser", _t_group_switch),
        _tool("get_github_trending", "获取 GitHub 当前趋势项目榜单（名称、描述、star 数）",
              {"type": "object", "properties": {
                  "since": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "默认 daily"},
                  "language": {"type": "string", "description": "编程语言筛选，如 python，留空为全部"},
              }, "required": []},
              "all", _t_gh_trending),
        _tool("get_current_time", "获取当前的日期、时间、星期与时区（回答任何与当前时间/日期/星期相关的问题前必须先调用）",
              {"type": "object", "properties": {}, "required": []},
              "all", _t_now),
        _tool("calculate", "进行精确的算术计算（加减乘除、取余、幂运算）",
              {"type": "object", "properties": {"expression": {"type": "string", "description": "算术表达式，如 (3+4)*2 或 2**10"}}, "required": ["expression"]},
              "all", _t_calc),
        _tool("list_replies", "查看关键词词库",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_reply_list),
        _tool("reload_replies", "重载关键词词库",
              {"type": "object", "properties": {}, "required": []},
              "superuser", _t_reply_reload),
    ]
    permitted = [pair for pair in tools if pair[1][0] != "superuser" or is_superuser]
    return permitted


async def chat(event: MessageEvent, text: str, bot=None) -> str | None:
    """调用 LLM（带工具调用循环）；返回回复文本，失败/被拦截返回 None（静默处理）。"""
    cfg = await ai_config()
    client = await get_client()
    if client is None:
        return None

    ok, reason = await _check_quota(event, cfg["ai_limit_group"], cfg["ai_limit_user"])
    if not ok:
        logger.info(f"AI 调用被限额拦截：{reason}")
        return None

    is_superuser = event.user_id in {
        int(u) for u in get_driver().config.superusers
    }
    tools = _build_tools(is_superuser)

    group_key = getattr(event, "group_id", 0) or -int(event.user_id)
    history = _get_history(group_key)
    scene = await _build_scene(bot, event)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": cfg["ai_system_prompt"]},
        {"role": "system", "content": scene},
        *history,
        {"role": "user", "content": text},
    ]

    reply: str | None = None
    try:
        # 工具调用循环：LLM 可连续调用工具，最多 MAX_TOOL_ROUNDS 轮
        for _ in range(MAX_TOOL_ROUNDS + 1):
            completion = await client.chat.completions.create(
                model=cfg["ai_model"], messages=messages,
                tools=[pair[0] for pair in tools] or None, temperature=0.7,
            )
            msg = completion.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                reply = (msg.content or "").strip()
                break

            messages.append(msg.model_dump(exclude_none=True))
            for call in tool_calls:
                name = call.function.name
                try:
                    args: dict[str, Any] = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool = next((pair for pair in tools if pair[0]["function"]["name"] == name), None)
                if tool is None:
                    result = f"错误：工具 {name} 不可用（权限不足或不存在）。"
                else:
                    try:
                        result = await tool[1][1](bot, event, args)
                    except Exception as e:
                        logger.exception(f"工具 {name} 执行失败")
                        result = f"执行失败：{e}（常见原因：机器人不是群管理员、成员不存在等）"
                logger.info(f"AI 工具调用 {name}({args}) -> {result[:120]}")
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        else:
            reply = None
    except Exception:
        logger.exception("AI 调用失败")
        return None
    if not reply:
        return None

    _append_history(group_key, "user", text, cfg["ai_ctx_rounds"])
    _append_history(group_key, "assistant", reply, cfg["ai_ctx_rounds"])
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "\n…（内容过长已截断）"
    return reply


# ---------------------------------------------------------------- 消息入口

ai_matcher = on_message(priority=11, block=False)


def _is_command(text: str) -> bool:
    start = {s for s in get_driver().config.command_start if s}
    return any(text.startswith(s) for s in start)


@ai_matcher.handle()
async def handle_ai(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    if not event.to_me:
        return
    text = event.message.extract_plain_text().strip()
    if not text or _is_command(text):
        return
    if not await is_ai_enabled():
        return  # mention 插件已处理固定回应
    # 群聊：已在白名单（规则）；私聊：仅超管（mention 层已放行超管私聊）
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent

    if isinstance(event, PrivateMessageEvent) and event.user_id not in {
        int(u) for u in get_driver().config.superusers
    }:
        return

    reply = await chat(event, text, bot=bot)
    if reply is None:
        return
    dev_id = get_config().xingchao_developer_id
    try:
        if isinstance(event, GroupMessageEvent):
            message = MessageSegment.at(event.user_id) + " "
        else:
            message = MessageSegment.at(event.user_id) + " "
        # {dev_at} 占位符 → 真实 @ 开发者
        for i, part in enumerate(reply.split("{dev_at}")):
            if i:
                message += MessageSegment.at(dev_id)
            if part:
                message += part
        await matcher.send(message)
    except MatcherException:
        raise
    except Exception:
        logger.exception("AI 回复发送失败")


# ---------------------------------------------------------------- /ai 指令

ai_cmd = on_command("ai", rule=SUPERUSER, priority=1, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("发送消息失败")


@ai_cmd.handle()
async def handle_ai_cmd(matcher: Matcher, args: Message = CommandArg()) -> None:
    from src.plugins import mention as mention_plugin

    raw = args.extract_plain_text().strip()
    action, _, rest = raw.partition(" ")
    if action == "on" or action == "off":
        if not (await is_configured()):
            await _send(ai_cmd, "AI 未配置 API 地址或密钥，请先在 Web 管理面板「AI」页填写。")
            return
        await get_store().set_kv("ai_enabled", "true" if action == "on" else "false")
        await _send(ai_cmd, f"AI 问答已{'开启' if action == 'on' else '关闭'}。")
        return
    if action == "status":
        cfg = await ai_config()
        usage = await _usage_payload()
        lines = [
            f"AI 问答：{'开启' if await is_ai_enabled() else '关闭'}"
            f"（{'已配置' if await is_configured() else '未配置 API'}）",
            f"模型: {cfg['ai_model']}",
            f"上下文: 每群 {cfg['ai_ctx_rounds']} 轮",
            f"今日限额: 每群 {cfg['ai_limit_group']} 次 / 每人 {cfg['ai_limit_user']} 次",
            f"今日用量: 群 {usage['groups']} | 人 {usage['users']}",
        ]
        await _send(ai_cmd, "\n".join(lines))
        return
    if action == "clear":
        gid = getattr(matcher.event, "group_id", None)
        if gid and _ctx.pop(gid, None) is not None:
            await _send(ai_cmd, "本群 AI 会话上下文已清空。")
        else:
            await _send(ai_cmd, "本群没有正在进行的 AI 会话。")
        return
    if action == "test" and rest.strip():
        cfg = await ai_config()
        client = await get_client()
        if client is None:
            await _send(ai_cmd, "AI 未配置（XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY）。")
            return
        try:
            completion = await client.chat.completions.create(
                model=cfg["ai_model"],
                messages=[{"role": "user", "content": rest.strip()}],
            )
            reply = (completion.choices[0].message.content or "").strip()
            await _send(ai_cmd, f"模型回复：\n{reply or '（空）'}")
        except Exception as e:
            logger.exception("AI 测试调用失败")
            await _send(ai_cmd, f"调用失败：{e}")
        return
    await _send(
        ai_cmd,
        "用法：/ai on|off、/ai status、/ai clear、/ai test <问题>\n"
        "（配置修改请在 Web 管理面板「AI」页操作）",
    )


# ---------------------------------------------------------------- 启动提示

driver = get_driver()


@driver.on_startup
async def _startup_hint() -> None:
    if await is_configured():
        cfg = await ai_config()
        logger.info(f"AI 问答就绪：模型 {cfg['ai_model']}（默认{'开启' if cfg['ai_enabled'] else '关闭'}）")
    else:
        logger.warning(
            "AI 问答未启用：缺少 XINGCHAO_AI_BASE_URL / XINGCHAO_AI_API_KEY。"
            "配置后并在面板「AI」页或 /ai on 开启即可使用。"
        )
