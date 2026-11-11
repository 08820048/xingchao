"""联网小工具：网页搜索、天气、汇率。给 AI 补上"知道此刻外面发生了什么"的能力。

- 搜索（/搜索 <关键词>；AI 工具 web_search）：提供商由环境变量 XINGCHAO_SEARCH_PROVIDER 决定
    * duckduckgo（默认，无需密钥，解析 html.duckduckgo.com；页面结构变了会返回空结果）
    * brave（需 XINGCHAO_SEARCH_API_KEY）
    * searxng（需 XINGCHAO_SEARCH_URL，指向自建实例，需开启 json 输出）
  全局每日调用上限 300 次，超限返回提示而不是继续请求
- 天气（/天气 <城市>；AI 工具 get_weather）：wttr.in，无需密钥，当前 + 未来三天
- 汇率（AI 工具 get_exchange_rate）：api.frankfurter.dev（欧洲央行参考汇率，每个工作日更新），无需密钥

返回给 AI 的都是纯文本摘要；网络失败只返回友好提示，不抛异常。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.config import get_config
from src.permission import BASIC

SEARCH_DAILY_LIMIT = 300
MAX_RESULTS = 5
TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; XingchaoBot/1.0; +https://xingchao.dev)"

_daily: tuple[str, int] = ("", 0)


def _bump_daily() -> bool:
    """今日搜索次数 +1；超限返回 False。"""
    global _daily
    day = datetime.now().strftime("%Y-%m-%d")
    d, n = _daily
    if d != day:
        d, n = day, 0
    if n >= SEARCH_DAILY_LIMIT:
        return False
    _daily = (d, n + 1)
    return True


# ---------------------------------------------------------------- 搜索

_TAG = re.compile(r"<[^>]+>")
_DDG_LINK = re.compile(r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.S)


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", s or "")).split())


def _unwrap_ddg(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    return unquote(q["uddg"][0]) if "uddg" in q else href


def parse_ddg(page: str, limit: int = MAX_RESULTS) -> list[dict[str, str]]:
    links = list(_DDG_LINK.finditer(page))
    out: list[dict[str, str]] = []
    for idx, m in enumerate(links):
        title = _clean(m.group("title"))
        url = _unwrap_ddg(m.group("href"))
        if not title or not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue  # 广告位
        end = links[idx + 1].start() if idx + 1 < len(links) else len(page)
        sm = _DDG_SNIPPET.search(page, m.end(), end)  # 只在本条与下一条之间找摘要
        out.append({"title": title, "url": url, "snippet": _clean(sm.group("snippet")) if sm else ""})
        if len(out) >= limit:
            break
    return out


class SearchBlocked(Exception):
    """搜索站点返回了机器人验证页（而不是搜索结果）。"""


async def _search_ddg(client: httpx.AsyncClient, q: str) -> list[dict[str, str]]:
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": q})
    r.raise_for_status()
    res = parse_ddg(r.text)
    if not res and (r.status_code == 202 or "anomaly" in r.text):
        raise SearchBlocked("duckduckgo 返回了机器人验证页")
    return res


async def _search_brave(client: httpx.AsyncClient, q: str, key: str) -> list[dict[str, str]]:
    r = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": MAX_RESULTS},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    r.raise_for_status()
    items = ((r.json() or {}).get("web") or {}).get("results") or []
    return [
        {"title": _clean(i.get("title", "")), "url": i.get("url", ""), "snippet": _clean(i.get("description", ""))}
        for i in items[:MAX_RESULTS]
    ]


async def _search_searxng(client: httpx.AsyncClient, q: str, base: str) -> list[dict[str, str]]:
    r = await client.get(base.rstrip("/") + "/search", params={"q": q, "format": "json"})
    r.raise_for_status()
    items = (r.json() or {}).get("results") or []
    return [
        {"title": _clean(i.get("title", "")), "url": i.get("url", ""), "snippet": _clean(i.get("content", ""))}
        for i in items[:MAX_RESULTS]
    ]


async def search(query: str) -> tuple[list[dict[str, str]] | None, str]:
    """返回 (结果列表, 提示)。结果为 None 表示失败，提示里是原因。"""
    query = " ".join(query.split())[:120]
    if not query:
        return None, "搜索词不能为空"
    cfg = get_config()
    provider = (cfg.xingchao_search_provider or "duckduckgo").lower()
    if provider == "brave" and not cfg.xingchao_search_api_key:
        return None, "搜索未配置：brave 需要 XINGCHAO_SEARCH_API_KEY"
    if provider == "searxng" and not cfg.xingchao_search_url:
        return None, "搜索未配置：searxng 需要 XINGCHAO_SEARCH_URL"
    if not _bump_daily():
        return None, f"今天的联网搜索次数已用完（{SEARCH_DAILY_LIMIT} 次），明天再试"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}) as client:
            if provider == "brave":
                res = await _search_brave(client, query, cfg.xingchao_search_api_key)
            elif provider == "searxng":
                res = await _search_searxng(client, query, cfg.xingchao_search_url)
            else:
                res = await _search_ddg(client, query)
    except SearchBlocked as e:
        logger.warning(f"联网搜索被拦截（{provider}）：{e}")
        return None, "搜索被 DuckDuckGo 拦截了（机器人验证），管理员需要改用 brave 或 searxng（见 .env.example）"
    except Exception as e:
        logger.warning(f"联网搜索失败（{provider}）：{type(e).__name__}: {e}")
        return None, f"搜索服务暂时连不上（{type(e).__name__}），如果一直这样，管理员需要检查服务器网络或换搜索提供商"
    return res, ""


def format_results(results: list[dict[str, str]]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet'][:120]}\n   {r['url']}" if r["snippet"]
                     else f"{i}. {r['title']}\n   {r['url']}")
    return "\n".join(lines)


async def search_text(query: str) -> str:
    res, note = await search(query)
    if res is None:
        return note
    if not res:
        return f"没搜到与「{query}」相关的结果。"
    return format_results(res)


# ---------------------------------------------------------------- 天气

def format_weather(city: str, data: dict[str, Any]) -> str:
    cur = (data.get("current_condition") or [{}])[0]

    def desc(d: dict) -> str:
        for key in ("lang_zh", "weatherDesc"):
            v = d.get(key)
            if v and isinstance(v, list) and v[0].get("value"):
                return str(v[0]["value"])
        return "未知"

    lines = [
        f"{city} 现在：{desc(cur)}，{cur.get('temp_C', '?')}°C（体感 {cur.get('FeelsLikeC', '?')}°C），"
        f"湿度 {cur.get('humidity', '?')}%，风 {cur.get('winddir16Point', '')} {cur.get('windspeedKmph', '?')}km/h"
    ]
    names = ["今天", "明天", "后天"]
    for i, day in enumerate((data.get("weather") or [])[:3]):
        hourly = day.get("hourly") or []
        mid = hourly[len(hourly) // 2] if hourly else {}
        lines.append(f"{names[i]}（{day.get('date', '')}）：{desc(mid)}，{day.get('mintempC', '?')}~{day.get('maxtempC', '?')}°C")
    return "\n".join(lines)


async def weather_text(city: str) -> str:
    city = city.strip()[:30]
    if not city:
        return "请告诉我要查哪个城市的天气。"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "curl/8"}) as client:
            r = await client.get(f"https://wttr.in/{quote(city)}", params={"format": "j1", "lang": "zh"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning(f"天气查询失败：{type(e).__name__}: {e}")
        return "天气服务暂时不可用，稍后再试"
    if not data.get("current_condition"):
        return f"没查到「{city}」的天气，换个城市写法试试（如：北京、Shanghai）。"
    return format_weather(city, data)


# ---------------------------------------------------------------- 汇率

_CCY = re.compile(r"^[A-Za-z]{3}$")


async def rate_text(src: str, dst: str, amount: float = 1.0) -> str:
    src, dst = src.strip().upper(), dst.strip().upper()
    if not (_CCY.match(src) and _CCY.match(dst)):
        return "币种请用三位代码，如 USD、CNY、JPY、EUR。"
    if not (0 < amount < 1e12):
        return "金额不合法。"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            r = await client.get("https://api.frankfurter.dev/v1/latest", params={"base": src, "symbols": dst, "amount": amount})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning(f"汇率查询失败：{type(e).__name__}: {e}")
        return "汇率服务暂时不可用，或不支持该币种。"
    val = (data.get("rates") or {}).get(dst)
    if val is None:
        return "没有查到该币种的汇率。"
    return f"{amount:g} {src} ≈ {val:,.2f} {dst}（欧洲央行参考汇率，{data.get('date', '')}）"


# ---------------------------------------------------------------- 指令

search_cmd = on_command("搜索", aliases={"search"}, rule=BASIC, priority=5, block=True)
weather_cmd = on_command("天气", aliases={"weather"}, rule=BASIC, priority=5, block=True)


@search_cmd.handle()
async def handle_search(matcher: Matcher, args: Message = CommandArg()) -> None:
    q = args.extract_plain_text().strip()
    if not q:
        await matcher.finish("用法：/搜索 <关键词>")
    await matcher.send(await search_text(q))


@weather_cmd.handle()
async def handle_weather(matcher: Matcher, args: Message = CommandArg()) -> None:
    city = args.extract_plain_text().strip()
    if not city:
        await matcher.finish("用法：/天气 <城市>")
    await matcher.send(await weather_text(city))
