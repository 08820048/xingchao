"""AI 工具：查询类：群知识库、聊天回忆、联网搜索、天气汇率、博客、帮助、记忆、计算等。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from src.aitools.registry import _j, tool
from src.config import get_config
from src.store import get_store


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


@tool(
    "search_group_kb",
    "查询本群知识库（群规、常见问题、报名/内测入口等）；用户问到群内规则或「以前说过的答案」时先用本工具查一次",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "要查的内容，如「怎么申请内测」"},
        "group_id": {"type": "integer", "description": "群号，群聊中可省略"},
    }, "required": ["query"]},
    "all",
)
async def _t_kb_search(bot, event, args) -> str:
    from src.plugins import kb

    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：群知识库只在群聊里可用，或需显式提供 group_id。"
    query = str(args.get("query", "")).strip()
    if not query:
        return "错误：需要 query。"
    entry = await kb.match(int(group_id), query, min_score=0.0)
    if entry is None:
        return f"知识库里没有和「{query}」相关的内容。"
    return f"知识 #{entry['id']}：{entry['question']}\n答案：{entry['answer']}"


@tool(
    "add_group_kb",
    "把一条问答加进本群知识库（之后有人问就会自动回答）",
    {"type": "object", "properties": {
        "question": {"type": "string", "description": "问题，如「怎么申请内测」"},
        "answer": {"type": "string", "description": "答案"},
        "keywords": {"type": "string", "description": "额外的同义关键词，空格分隔"},
        "group_id": {"type": "integer"},
    }, "required": ["question", "answer"]},
    "superuser",
)
async def _t_kb_add(bot, event, args) -> str:
    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：需要在群聊中添加，或显式提供 group_id。"
    question = str(args.get("question", "")).strip()
    answer = str(args.get("answer", "")).strip()
    if not (question and answer):
        return "错误：question 与 answer 都必填。"
    entry_id = await get_store().add_kb_entry(
        int(group_id), question[:200], answer[:2000], str(args.get("keywords", ""))[:300],
        source="ai", created_by=int(event.user_id),
    )
    return f"已添加群知识 #{entry_id}：{question}"


@tool(
    "suggest_group_kb",
    "列出最近被反复问、但知识库还没收录的问题（可直接加进知识库）",
    {"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": []},
    "superuser",
)
async def _t_kb_suggest(bot, event, args) -> str:
    from src.plugins import kb

    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：需要群号。"
    items = await kb.suggest(int(group_id))
    if not items:
        return "最近没有反复被问、又不在知识库里的问题。"
    return _j({"候选问题": items[:10]})


@tool(
    "recall_chat",
    "检索本群过去的聊天记录（用户说“上次谁说过××”“之前发过的那个链接/表格”“前几天聊的那个事”时使用；query 填关键词，days 填回溯天数如 7=一周、30=一个月，不填用默认 90 天）",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "关键词，尽量用具体的名词"},
        "days": {"type": "integer", "description": "回溯天数；用户说“上周”填 7，“上个月”填 30"},
        "group_id": {"type": "integer", "description": "群号，群聊中可省略"},
    }, "required": ["query"]},
    "all",
)
async def _t_recall_chat(bot, event, args) -> str:
    from src.plugins import recall

    group_id = args.get("group_id") or getattr(event, "group_id", None)
    if not group_id:
        return "错误：群聊回忆只能在群聊里用，或显式提供 group_id。"
    query = str(args.get("query", "")).strip()
    if not query:
        return "错误：需要 query（要回忆的关键词）。"
    days = args.get("days")
    try:
        days = int(days) if days is not None else None
    except (TypeError, ValueError):
        days = None
    return await recall.recall_text(bot, int(group_id), query, days=days)


@tool(
    "web_search",
    "联网搜索网页（需要最新信息、新闻、版本号、你不确定的事实时使用；返回标题/摘要/发布时间/链接，回答时说明信息来自网络搜索；搜不到就如实说搜不到，不要编造结果）",
    {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
    "all",
)
async def _t_web_search(bot, event, args) -> str:
    from src.plugins import webtools
    return await webtools.search_text(str(args.get("query", "")))


@tool(
    "get_weather",
    "查询城市天气（当前 + 未来三天；用户问“北京天气怎么样/明天下雨吗”时使用）",
    {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    "all",
)
async def _t_weather(bot, event, args) -> str:
    from src.plugins import webtools
    return await webtools.weather_text(str(args.get("city", "")))


@tool(
    "get_exchange_rate",
    "查询汇率/换算金额（币种用三位代码，如 USD CNY JPY EUR；用户问“100美元多少人民币”时使用）",
    {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "amount": {"type": "number"}}, "required": ["from", "to"]},
    "all",
)
async def _t_rate(bot, event, args) -> str:
    from src.plugins import webtools
    try:
        amount = float(args.get("amount", 1) or 1)
    except (TypeError, ValueError):
        amount = 1.0
    return await webtools.rate_text(str(args.get("from", "")), str(args.get("to", "")), amount)


@tool(
    "get_github_trending",
    "获取 GitHub 当前趋势项目榜单（名称、描述、star 数）",
    {"type": "object", "properties": {
        "since": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "默认 daily"},
        "language": {"type": "string", "description": "编程语言筛选，如 python，留空为全部"},
    }, "required": []},
    "all",
)
async def _t_gh_trending(bot, event, args) -> str:
    from src.plugins import github as gh
    since = str(args.get("since", "daily"))
    language = str(args.get("language", ""))
    try:
        items = await gh.fetch_trending(since, language)
        return _j({"趋势榜": items})
    except Exception as e:
        return f"获取趋势失败：{e}"


@tool(
    "search_blog",
    "搜索开发者博客（xuyi.dev）的文章与作品集。当用户求推荐软件/工具、求助技术问题、聊到编程学习（Git、Rust、Unity、AI 开发等）或可能与博主作品相关的话题时，先调用本工具查询；找到相关内容后优先推荐",
    {"type": "object", "properties": {"keywords": {"type": "string", "description": "主题关键词，如 Mac 剪贴板、git 分支、端口占用"}}, "required": ["keywords"]},
    "all",
)
async def _t_blog_search(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    query = str(args.get("keywords", "")).strip()
    if not query:
        return "错误：需要 keywords（要搜索的主题关键词，如 Mac 剪贴板、git 分支、端口占用）。"
    if not await blog_plugin.is_enabled():
        return "博客知识关联推荐当前已关闭（超管可开启）。"
    hits = await blog_plugin.search(query, limit=5)
    if not hits:
        return "博客知识库中没有找到与该主题相关的内容（可能尚未就绪，或确实没有相关文章/作品）。"
    return _j({"结果（优先级由高到低）": hits})


@tool(
    "get_blog_post",
    "读取某篇博客文章的摘要与正文节选（先用 search_blog 找到文章，再按标题或链接读取）",
    {"type": "object", "properties": {"url_or_title": {"type": "string", "description": "文章链接或标题关键词"}}, "required": ["url_or_title"]},
    "all",
)
async def _t_blog_post(bot, event, args) -> str:
    from src.plugins import blog as blog_plugin
    target = str(args.get("url_or_title", "")).strip()
    if not target:
        return "错误：需要 url_or_title（文章链接或标题关键词）。"
    post = await blog_plugin.get_post(target)
    if not post:
        return "错误：未找到该文章，可先用 search_blog 搜索主题。"
    return _j({
        "标题": post.get("title"), "分类": post.get("category"),
        "发布日期": post.get("published"), "链接": post.get("url"),
        "摘要": post.get("summary"), "正文节选": post.get("excerpt"),
    })


@tool(
    "get_ad_policy",
    "查看群广告招商政策（招商说明、报价、联系人、合作方名单与命中处理方式）；有人问“你们接广告吗/广告怎么收费”时先调用本工具",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_ad_policy(bot, event, args) -> str:
    from src.plugins import ads as ads_plugin

    cfg = await ads_plugin.ad_config()
    return _j({
        "招商开启": cfg["ad_enabled"],
        "招商说明": cfg["ad_text"],
        "报价": cfg["ad_price"],
        "联系人QQ": cfg["ad_contact"],
        "合作方QQ": cfg["ad_partners"],
        "命中处理": ads_plugin.ACTION_LABEL.get(cfg["ad_action"], cfg["ad_action"]),
        "命中通知超管": cfg["ad_notify"],
        "提示冷却秒": cfg["ad_cooldown"],
    })


@tool(
    "get_changelog",
    "查看机器人最近一次更新了什么（用户问“最近更新了啥/有什么新功能”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_changelog(bot, event, args) -> str:
    from src.plugins.updates import format_entry, load_changelog
    entries = load_changelog()
    if not entries:
        return _j({"更新记录": "暂无"})
    return _j({"最近一次更新": format_entry(entries[0])})


@tool(
    "get_help",
    "查看完整指令菜单（仅当用户明确索要完整功能/指令列表、完整帮助时才调用；用户只是问“你能做什么”时用你自己的话自然介绍，不要调用本工具）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_help(bot, event, args) -> str:
    from src import helpmenu
    from src.plugins.basic import _is_group_admin
    return helpmenu.render_menu(await _is_group_admin(bot, event))


@tool(
    "get_about",
    "获取机器人与开发者信息",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_about(bot, event, args) -> str:
    cfg = get_config()
    return _j({
        "机器人": "星潮（开源 QQ 群助手机器人）",
        "开发者": "XuYi",
        "开发者QQ": cfg.xingchao_developer_id,
        "开发者博客": cfg.xingchao_developer_blog,
        "官网": cfg.xingchao_developer_site,
        "开源地址": "https://github.com/08820048/xingchao",
    })


@tool(
    "get_current_time",
    "获取当前的日期、时间、星期与时区（回答任何与当前时间/日期/星期相关的问题前必须先调用）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
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


@tool(
    "calculate",
    "进行精确的算术计算（加减乘除、取余、幂运算）",
    {"type": "object", "properties": {"expression": {"type": "string", "description": "算术表达式，如 (3+4)*2 或 2**10"}}, "required": ["expression"]},
    "all",
)
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


@tool(
    "ping",
    "连通测试",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_ping(bot, event, args) -> str:
    return _j({"连通": "pong", "self_id": bot.self_id})


@tool(
    "get_my_id",
    "查看当前群号 / 用户 QQ / 机器人 ID",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_my_id(bot, event, args) -> str:
    gid = getattr(event, "group_id", None)
    return _j({"group_id": gid, "user_id": event.user_id, "self_id": bot.self_id, "环境": "群聊" if gid else "私聊"})


@tool(
    "get_my_memory",
    "查看你（机器人）对当前提问者记住了什么（长期记忆里关于 TA 的印象与熟悉度；用户问“你记得我什么/你了解我吗”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_memory_get(bot, event, args) -> str:
    from src.plugins import memory
    gid = event.group_id if isinstance(event, GroupMessageEvent) else None
    return await memory.describe_memory(event.user_id, gid)


@tool(
    "forget_my_memory",
    "清除对当前提问者的全部印象并停止记录 TA 的发言（用户说“忘了我/别记我/清除我的记忆”时使用，执行前简短确认）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_memory_forget(bot, event, args) -> str:
    store = get_store()
    removed = await store.delete_user_memories(event.user_id)
    await store.set_memory_optout(event.user_id, True)
    return f"已清除对该用户的全部印象（{removed} 个群），并停止记录其发言；对方可说“记住我”恢复。"


@tool(
    "resume_my_memory",
    "恢复记录当前提问者（用户说“可以记住我了/重新记我”时使用）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_memory_resume(bot, event, args) -> str:
    await get_store().set_memory_optout(event.user_id, False)
    return "已恢复记录，之后会重新慢慢了解对方。"
