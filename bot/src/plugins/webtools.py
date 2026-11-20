"""联网小工具：网页搜索、天气、汇率。给 AI 补上"知道此刻外面发生了什么"的能力。

- 搜索（/搜索 <关键词>；AI 工具 web_search）：按**降级链**依次尝试多个源，前一个失败自动换下一个
    * deepseek（默认，复用 AI 的 DeepSeek 密钥，POST api.deepseek.com/v1/tools/search，
      返回结构化结果：摘要、正文摘要、发布日期、权威度；失败不计费）
    * duckduckgo（免密钥，解析 html.duckduckgo.com；机房 IP 常被判为机器人，仅作备选）
    * brave（需 XINGCHAO_SEARCH_API_KEY）
    * searxng（需 XINGCHAO_SEARCH_URL，指向自建实例，需开启 json 输出）
  链由面板「AI → 联网搜索」或 XINGCHAO_SEARCH_PROVIDERS 决定；每日上限、缓存时长也可配
  （kv，即时生效，无需重启）。只有**成功**的搜索才计入当日额度，重启不清零；
  同一 query 在缓存期内复用结果，不打网络
- 天气（/天气 <城市>；AI 工具 get_weather）：wttr.in，无需密钥，当前 + 未来三天
- 汇率（AI 工具 get_exchange_rate）：api.frankfurter.dev（欧洲央行参考汇率，每个工作日更新），无需密钥

返回给 AI 的都是纯文本摘要；网络失败只返回友好提示，不抛异常。
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.httpclient import get_http
from src.config import get_config
from src.permission import BASIC

SEARCH_DAILY_LIMIT = 300
MAX_RESULTS = 5
TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; XingchaoBot/1.0; +https://xingchao.dev)"

# 可用搜索源。deepseek 走 DeepSeek 官方搜索 API（结构化结果、带摘要与权威度，
# 失败不计费）；其余三个沿用原来的抓取/第三方 API 实现。
PROVIDERS = ("deepseek", "duckduckgo", "brave", "searxng")
PROVIDER_LABEL = {
    "deepseek": "DeepSeek 官方搜索",
    "duckduckgo": "DuckDuckGo（网页抓取）",
    "brave": "Brave Search API",
    "searxng": "SearXNG 自建实例",
}
DEFAULT_CHAIN = ("deepseek", "duckduckgo")

# 面板可改（kv），空值回落到环境变量 / 默认链
DEFAULTS: dict[str, object] = {
    "search_providers": "",        # JSON 数组，降级链顺序
    "search_daily_limit": SEARCH_DAILY_LIMIT,
    "search_cache_ttl": 600,       # 同 query 结果复用秒数，0 = 不缓存
    "search_need_content": False,  # deepseek 是否顺带抓正文（更慢更占带宽）
}

CACHE_MAX = 200
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _reset_state() -> None:
    """测试用：清空结果缓存。"""
    _cache.clear()


async def _kv(key: str) -> Any:
    from src.store import get_store

    raw = await get_store().get_kv(key)
    default = DEFAULTS[key]
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw == "true"
    if isinstance(default, int):
        try:
            return max(0, int(raw))
        except ValueError:
            return default
    return raw


def _parse_chain(raw: object) -> list[str]:
    """解析搜索源链：JSON 数组 / 逗号分隔 / 单个名字。"""
    items: list[object]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = None
        items = data if isinstance(data, list) else [p for p in re.split(r"[,，\s]+", text) if p]
    out: list[str] = []
    for item in items:
        name = str(item).strip().lower()
        if name in PROVIDERS and name not in out:
            out.append(name)
    return out


async def search_chain() -> list[str]:
    """生效的搜索源顺序：面板 kv > env XINGCHAO_SEARCH_PROVIDERS > 单源 env > 默认链。"""
    chain = _parse_chain(await _kv("search_providers"))
    if chain:
        return chain
    cfg = get_config()
    chain = _parse_chain(getattr(cfg, "xingchao_search_providers", ""))
    if chain:
        return chain
    single = _parse_chain(getattr(cfg, "xingchao_search_provider", ""))
    return single or list(DEFAULT_CHAIN)


async def deepseek_creds() -> tuple[str, str]:
    """DeepSeek 搜索用的 (base, key)。

    搜索 API 只在 api.deepseek.com 上提供，所以复用 AI 的 DeepSeek 密钥：
    优先「DeepSeek 提供商」单独填的密钥，其次默认提供商指向 deepseek 时的密钥。
    """
    try:
        from src.plugins import ai as ai_plugin

        ds = await ai_plugin.deepseek_settings()
        if ds["api_key"]:
            return "https://api.deepseek.com", ds["api_key"]
        base, key = await ai_plugin._ai_creds()
        if key and "deepseek.com" in (base or ""):
            return "https://api.deepseek.com", key
    except Exception:
        logger.debug("读取 DeepSeek 搜索凭据失败", exc_info=True)
    return "", ""


async def provider_status() -> list[dict[str, Any]]:
    """每个搜索源当前是否可用（面板展示用）。"""
    cfg = get_config()
    _, key = await deepseek_creds()
    out = []
    for name in PROVIDERS:
        if name == "deepseek":
            ready = bool(key)
            note = "已配置 DeepSeek 密钥" if ready else "缺少 DeepSeek API 密钥"
        elif name == "brave":
            ready = bool(cfg.xingchao_search_api_key)
            note = "已配置" if ready else "需要 XINGCHAO_SEARCH_API_KEY"
        elif name == "searxng":
            ready = bool(cfg.xingchao_search_url)
            note = "已配置" if ready else "需要 XINGCHAO_SEARCH_URL"
        else:
            ready, note = True, "无需密钥（可能被 DDG 拦截）"
        out.append({"key": name, "label": PROVIDER_LABEL[name], "ready": ready, "note": note})
    return out


async def _used_today() -> int:
    from src.store import get_store

    raw = await get_store().get_kv("search_counter") or ""
    day, _, count = raw.partition(":")
    if day != datetime.now().strftime("%Y-%m-%d"):
        return 0
    try:
        return max(0, int(count or 0))
    except ValueError:
        return 0


async def _bump_daily() -> bool:
    """成功搜索后计数 +1（持久化到 kv，重启不清零）；超限返回 False。

    只有真正拿到结果才计数——失败的请求不消耗当天额度。
    """
    from src.store import get_store

    limit = int(await _kv("search_daily_limit"))
    day = datetime.now().strftime("%Y-%m-%d")
    used = await _used_today()
    if limit and used >= limit:
        return False
    await get_store().set_kv("search_counter", f"{day}:{used + 1}")
    return True


def _cache_get(key: str, ttl: int) -> list[dict[str, str]] | None:
    if ttl <= 0:
        return None
    hit = _cache.get(key)
    if not hit:
        return None
    ts, results = hit
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return results


def _cache_put(key: str, results: list[dict[str, str]]) -> None:
    if len(_cache) >= CACHE_MAX:
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), results)


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


async def _search_deepseek(client: httpx.AsyncClient, q: str) -> list[dict[str, str]]:
    """DeepSeek 官方搜索 API（结构化结果，失败请求不计费）。"""
    base, key = await deepseek_creds()
    if not key:
        raise RuntimeError("未配置 DeepSeek 密钥")
    r = await client.post(
        base.rstrip("/") + "/v1/tools/search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "query": q,
            "count": MAX_RESULTS,
            "need_content": bool(await _kv("search_need_content")),
        },
    )
    r.raise_for_status()
    out: list[dict[str, str]] = []
    for it in ((r.json() or {}).get("results") or [])[:MAX_RESULTS]:
        title = _clean(it.get("title", ""))
        url = str(it.get("url") or "")
        if not title or not url.startswith("http"):
            continue
        entry: dict[str, str] = {
            "title": title,
            "url": url,
            # snippet 短（100-200 字）适合展示，summary 长（600-800 字）更适合给模型读
            "snippet": _clean(it.get("snippet", "")),
            "summary": _clean(it.get("summary", "")),
            "date": str(it.get("date") or ""),
            "site": _clean(it.get("site_name") or ""),
        }
        out.append(entry)
    return out


async def _search_duckduckgo(client: httpx.AsyncClient, q: str) -> list[dict[str, str]]:
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": q})
    r.raise_for_status()
    res = parse_ddg(r.text)
    if not res and (r.status_code == 202 or "anomaly" in r.text):
        raise SearchBlocked("duckduckgo 返回了机器人验证页")
    return res


async def _search_brave(client: httpx.AsyncClient, q: str) -> list[dict[str, str]]:
    r = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": MAX_RESULTS},
        headers={
            "X-Subscription-Token": get_config().xingchao_search_api_key,
            "Accept": "application/json",
        },
    )
    r.raise_for_status()
    items = ((r.json() or {}).get("web") or {}).get("results") or []
    return [
        {"title": _clean(i.get("title", "")), "url": i.get("url", ""), "snippet": _clean(i.get("description", ""))}
        for i in items[:MAX_RESULTS]
    ]


async def _search_searxng(client: httpx.AsyncClient, q: str) -> list[dict[str, str]]:
    base = get_config().xingchao_search_url
    r = await client.get(base.rstrip("/") + "/search", params={"q": q, "format": "json"})
    r.raise_for_status()
    items = (r.json() or {}).get("results") or []
    return [
        {"title": _clean(i.get("title", "")), "url": i.get("url", ""), "snippet": _clean(i.get("content", ""))}
        for i in items[:MAX_RESULTS]
    ]


_RUNNERS = {
    "deepseek": _search_deepseek,
    "duckduckgo": _search_duckduckgo,
    "brave": _search_brave,
    "searxng": _search_searxng,
}


async def _run_provider(name: str, query: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        return await _RUNNERS[name](client, query)


async def search(query: str) -> tuple[list[dict[str, str]] | None, str]:
    """按降级链搜索，返回 (结果列表, 提示)。

    None = 所有源都失败（提示是给用户看的友好文案，不含内部配置名）。
    [] = 所有源都正常但没有结果。成功的搜索才计入当日额度。
    """
    query = " ".join(query.split())[:120]
    if not query:
        return None, "搜索词不能为空"

    chain = await search_chain()
    if not chain:
        return None, "搜索暂不可用：管理员还没有配置搜索源。"

    limit = int(await _kv("search_daily_limit"))
    if limit and await _used_today() >= limit:
        return None, f"今天的联网搜索次数已用完（{limit} 次），明天再试"

    ttl = int(await _kv("search_cache_ttl"))
    cache_key = f"{'|'.join(chain)}::{query}".lower()
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached, ""

    errors: list[str] = []
    succeeded = False  # 至少有一个源正常响应（哪怕结果为空）
    for name in chain:
        try:
            results = await _run_provider(name, query)
        except SearchBlocked as e:
            logger.warning(f"联网搜索被拦截（{name}）：{e}")
            errors.append(f"{name}:被拦截")
            continue
        except Exception as e:
            logger.warning(f"联网搜索失败（{name}）：{type(e).__name__}: {e}")
            errors.append(f"{name}:{type(e).__name__}")
            continue

        succeeded = True
        if results:
            await _bump_daily()
            _cache_put(cache_key, results)
            return results, ""
        errors.append(f"{name}:无结果")

    # 有源正常回答了"没结果" -> 就是真没搜到，别报成服务故障
    if succeeded:
        _cache_put(cache_key, [])
        return [], ""
    logger.warning(f"联网搜索全部失败：{'；'.join(errors)}")
    return None, (
        "联网搜索暂时不可用，稍后再试；"
        "如果一直这样，管理员可以在面板「AI → 联网搜索」里换个搜索源"
    )


def format_results(results: list[dict[str, str]]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        head = f"{i}. {r['title']}"
        if r.get("site") and r["site"] not in r["title"]:
            head += f"（{r['site']}）"
        block = [head]
        # 优先用长摘要（deepseek 的 summary），退回短摘要
        body = (r.get("summary") or r.get("snippet") or "").strip()[:400]
        if body:
            block.append(f"   {body}")
        if r.get("date"):
            block.append(f"   发布：{r['date'][:10]}")
        block.append(f"   {r['url']}")
        lines.append("\n".join(block))
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
        r = await get_http().get(
            f"https://wttr.in/{quote(city)}", params={"format": "j1", "lang": "zh"},
            headers={"User-Agent": "curl/8"}, timeout=TIMEOUT,
        )
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
        r = await get_http().get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": src, "symbols": dst, "amount": amount}, timeout=TIMEOUT,
        )
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
