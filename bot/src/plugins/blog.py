"""博客知识关联推荐：本地索引 xuyi.dev 的文章与作品集，供 AI 优先推荐。

- 后台刷新：启动后即检查，之后每 6 小时检查一次，距上次超过 24 小时才重新抓取
  RSS；抓取失败保留旧索引并记录错误（面板可见，下轮重试）。
- 对 AI 暴露：
  - `context_for(text)`：直接对话（ai.py chat()）注入「短提示 + 命中条目」；
  - `recall(text)`：主动插话（proactive.py）用；强命中时降低插话门槛；
  - `search()/get_post()`：AI 工具 search_blog / get_blog_post。
- 管理：`/blog status|refresh|list|on|off`（超管）、面板「博客」页、
  AI 工具 refresh_blog / set_blog_enabled（超管自然语言管理）。
- 开关 blog_enabled，默认开启；关闭后所有注入与查询静默失效。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from nonebot import get_driver, on_command
from nonebot.adapters import Message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src import blog_kb
from src.permission import SUPERUSER
from src.store import get_store

_REFRESH_INTERVAL = timedelta(hours=24)
_CHECK_INTERVAL = 6 * 3600
_PAGE_STALE_DAYS = 30  # 较老文章（RSS 不含）单独抓页面的重抓周期

HINT = (
    "【博客知识提示】本机器人的开发者（博主）运营个人博客 xuyi.dev，收录了作品集"
    "（如 Clibo 剪贴板工具、Berth 端口工具、Chupin 简历等 12 款产品）与技术文章"
    "（Git/版本控制、Mac/开发工具、AI、独立开发、游戏开发等 70+ 篇）。"
    "当用户的话题涉及软件/工具推荐、编程学习、技术求助、独立开发等领域时，"
    "先调用 search_blog 查询是否有相关文章或作品；查到相关内容则优先用于回答并自然推荐"
    "（给出名称与链接，不要生硬推销；与问题无关就不要提）。"
)

_index: blog_kb.BlogIndex | None = None
_refresh_lock = asyncio.Lock()
_worker_started = False


# ---------------------------------------------------------------- 配置 / 状态


async def is_enabled() -> bool:
    raw = await get_store().get_kv("blog_enabled")
    return raw != "false"


async def set_enabled(enabled: bool) -> None:
    await get_store().set_kv("blog_enabled", "true" if enabled else "false")


async def _last_refresh() -> datetime | None:
    raw = await get_store().get_kv("blog_last_refresh")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def _is_stale() -> bool:
    last = await _last_refresh()
    if last is None:
        return True
    return datetime.now().astimezone() - last >= _REFRESH_INTERVAL


# ---------------------------------------------------------------- 刷新 / 索引


def _fresh_enough(fetched_at: str, days: int) -> bool:
    try:
        return datetime.now().astimezone() - datetime.fromisoformat(fetched_at) < timedelta(days=days)
    except (TypeError, ValueError):
        return False


async def refresh(force: bool = False) -> dict[str, Any]:
    """抓取 RSS（+ sitemap 补齐较老文章）并全量重建索引；失败保留旧数据。"""
    global _index
    async with _refresh_lock:
        if not force and not await _is_stale():
            return {"ok": True, "count": await get_store().count_blog_posts(), "skipped": True}
        now = datetime.now().astimezone()
        try:
            feed_bytes = await blog_kb.fetch_feed()
            feed_posts = blog_kb.parse_feed(feed_bytes)
            if not feed_posts:
                raise ValueError("RSS 解析结果为空")
            feed_urls = {p["url"] for p in feed_posts}

            existing = {p["url"]: p for p in await get_store().list_blog_posts()}
            sitemap_urls: list[str] = []
            try:
                sitemap_urls = blog_kb.parse_sitemap(await blog_kb.fetch_sitemap())
            except Exception as e:
                logger.warning(f"抓取 sitemap 失败，本次仅使用 RSS：{e}")

            # sitemap 有、RSS 没有的较老文章：缺失或超过 30 天的才单篇抓取
            to_fetch = [
                url for url in sitemap_urls
                if url not in feed_urls
                and not (existing.get(url)
                         and _fresh_enough(existing[url].get("fetched_at", ""), _PAGE_STALE_DAYS))
            ]
            fetched = await blog_kb.fetch_pages(to_fetch)

            merged: dict[str, dict[str, Any]] = {}
            for post in feed_posts:
                merged[post["url"]] = post
            for url in sitemap_urls:
                if url in feed_urls:
                    continue
                if url in fetched:
                    merged[url] = fetched[url]
                elif url in existing:
                    merged[url] = {k: existing[url].get(k, "") for k in
                                   ("url", "title", "summary", "category", "published", "excerpt")}
                    merged[url]["fetched_at"] = existing[url].get("fetched_at", "")
            posts = list(merged.values())
            if not posts:
                raise ValueError("合并后文章列表为空")

            await get_store().replace_blog_posts(posts)
            _index = blog_kb.BlogIndex(posts)
            await get_store().set_kv("blog_last_refresh", now.isoformat(timespec="seconds"))
            await get_store().set_kv("blog_last_error", "")
            logger.info(f"博客知识库已刷新：{len(posts)} 篇文章（新抓页面 {len(fetched)} 篇）")
            return {"ok": True, "count": len(posts),
                    "time": now.strftime("%Y-%m-%d %H:%M")}
        except Exception as e:
            message = str(e) or type(e).__name__
            await get_store().set_kv(
                "blog_last_error", f"{now.strftime('%Y-%m-%d %H:%M')} {message}"[:300]
            )
            logger.warning(f"博客知识库刷新失败：{message}")
            return {"ok": False, "error": message,
                    "count": await get_store().count_blog_posts()}


async def _get_index() -> blog_kb.BlogIndex | None:
    global _index
    if _index is None:
        posts = await get_store().list_blog_posts()
        if posts:
            _index = blog_kb.BlogIndex(posts)
    return _index


async def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    if not await is_enabled():
        return []
    try:
        index = await _get_index()
        return index.search(query, limit) if index else []
    except Exception:
        logger.exception("博客知识库查询失败")
        return []


async def get_post(url_or_title: str) -> dict[str, Any] | None:
    try:
        index = await _get_index()
        return index.get_post(url_or_title) if index else None
    except Exception:
        logger.exception("博客文章查找失败")
        return None


async def recall(text: str) -> dict[str, Any] | None:
    """相关性命中（含作品与文章）；未命中/未就绪返回 None，永不抛错。"""
    if not await is_enabled():
        return None
    try:
        index = await _get_index()
        return index.recall(text) if index else None
    except Exception:
        logger.exception("博客知识召回失败")
        return None


def format_knowledge(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for p in result.get("products", []):
        lines.append(f"- [作品] {p['title']}：{p['summary']} → {p['url']}")
    for p in result.get("posts", []):
        category = f"[{p['category']}] " if p.get("category") else ""
        lines.append(f"- [文章] {category}{p['title']} → {p['url']}")
    return "\n".join(lines)


async def context_for(text: str) -> str:
    """ai.py chat() 注入：始终带短提示；命中时附具体条目。"""
    if not await is_enabled():
        return ""
    result = await recall(text)
    if not result:
        return HINT
    return (
        HINT
        + "\n\n【博主内容·与当前话题可能相关】以下内容来自 xuyi.dev，"
        "如确认与用户问题相关，请优先据此回答或推荐（自然给出名称与链接）：\n"
        + format_knowledge(result)
    )


async def status() -> dict[str, Any]:
    """面板/命令共用的状态数据（永不抛错）。"""
    store = get_store()
    try:
        posts = await store.list_blog_posts()
    except Exception:
        posts = []
    posts.sort(key=lambda p: p.get("published") or "", reverse=True)
    return {
        "enabled": await is_enabled(),
        "last_refresh": await store.get_kv("blog_last_refresh") or "",
        "last_error": await store.get_kv("blog_last_error") or "",
        "count": len(posts),
        "products": [
            {"name": p["name"], "url": p["url"], "tagline": p["tagline"], "status": p["status"]}
            for p in blog_kb.PRODUCTS
        ],
        "posts": [
            {
                "title": p.get("title", ""), "url": p.get("url", ""),
                "category": p.get("category", ""), "published": p.get("published", ""),
                "summary": p.get("summary", ""),
            }
            for p in posts
        ],
    }


# ---------------------------------------------------------------- 后台刷新

@get_driver().on_startup
async def _start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    asyncio.get_event_loop().create_task(_worker())


async def _worker() -> None:
    while True:
        try:
            if await is_enabled() and await _is_stale():
                await refresh(force=True)
        except Exception:
            logger.exception("博客知识库后台刷新异常")
        await asyncio.sleep(_CHECK_INTERVAL)


# ---------------------------------------------------------------- 指令

blog_cmd = on_command("blog", rule=SUPERUSER, priority=1, block=True)


@blog_cmd.handle()
async def handle_blog(matcher: Matcher, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip().lower()
    if raw in ("", "status", "状态"):
        data = await status()
        await matcher.send(
            f"博客知识库：{'已开启' if data['enabled'] else '已关闭'}\n"
            f"文章 {data['count']} 篇 / 作品 {len(data['products'])} 个\n"
            f"最近刷新：{data['last_refresh'] or '尚未刷新'}\n"
            f"最近错误：{data['last_error'] or '无'}\n"
            "用法：/blog refresh 立即刷新；/blog list [数量] 查看文章；/blog on|off 开关"
        )
        return
    if raw in ("refresh", "刷新", "reload", "更新"):
        await matcher.send("正在刷新博客知识库（抓取 xuyi.dev RSS）…")
        result = await refresh(force=True)
        if result.get("ok"):
            await matcher.send(f"刷新完成：共 {result['count']} 篇文章（{result.get('time', '')}）。")
        else:
            await matcher.send(f"刷新失败：{result.get('error', '未知错误')}（沿用旧数据，稍后自动重试）")
        return
    if raw.startswith("list") or raw in ("列表",):
        parts = raw.split()
        count = 10
        if len(parts) > 1 and parts[1].isdigit():
            count = max(1, min(int(parts[1]), 30))
        data = await status()
        posts = data["posts"][:count]
        if not posts:
            await matcher.send("知识库还没有文章，可先发送 /blog refresh。")
            return
        lines = [f"博客文章（最新 {len(posts)} 篇）："]
        for p in posts:
            category = f"[{p['category']}] " if p.get("category") else ""
            lines.append(f"· {p.get('published', '')} {category}{p['title']}")
            lines.append(f"  {p['url']}")
        await matcher.send("\n".join(lines))
        return
    if raw in ("on", "off"):
        enabled = raw == "on"
        await set_enabled(enabled)
        await matcher.send(f"博客知识关联推荐已{'开启' if enabled else '关闭'}。")
        return
    await matcher.send("用法：/blog status | refresh | list [数量] | on | off")
