"""xuyi.dev 博客知识库：RSS 抓取 / 解析 / 相关性召回（纯逻辑，不依赖 nonebot）。

设计：
- 文章：从 https://xuyi.dev/feed.xml 抓取（RSS 含全量文章与正文），解析出
  标题 / 链接 / 摘要 / 分类 / 日期 / 正文节选；
- 作品集：12 款产品由常量维护（别名 alias 是相关性匹配的核心资产）；
- 索引：标题/分类/别名提取「核心词」，正文节选取「辅助词」（低权重）；
  中文按 2-4 字 n-gram、英文按单词建倒排索引，用 IDF 抑制常见词；
- 召回：对消息文本打分（词长 × IDF × 权重），超过阈值即命中。

本模块可脱离 nonebot 单独运行测试（见仓库脚本/容器内 python -c）。
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import re
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

FEED_URL = "https://xuyi.dev/feed.xml"
SITEMAP_URL = "https://xuyi.dev/sitemap.xml"
SITE_URL = "https://xuyi.dev"
USER_AGENT = "XingchaoBot/1.0 (+https://xingchao.dev)"
MAX_FEED_BYTES = 4 * 1024 * 1024
EXCERPT_CHARS = 800
PAGE_CONCURRENCY = 4
PAGE_TIMEOUT = 15.0

MIN_SCORE = 5.0          # 文章命中阈值
MIN_PRODUCT_SCORE = 8.0  # 作品命中阈值（避免泛词误报）
STRONG_SCORE = 9.0       # 强命中阈值（主动接话加速用）

# ---------------------------------------------------------------- 作品集（人工维护）

PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "Clibo",
        "url": "https://clibo.us",
        "tagline": "Mac 剪贴板历史管理工具，「复制过的东西，也可以有迹可循」",
        "aliases": ["剪贴板", "剪切板", "粘贴板", "剪贴板管理", "复制历史", "clipboard", "clibo", "复制粘贴记录"],
        "status": "付费",
    },
    {
        "name": "Hoolo",
        "url": "https://hoolo.cc",
        "tagline": "Mac 文件整理工具，「妈妈再也不用担心我的 Mac 像狗窝」",
        "aliases": ["文件整理", "桌面整理", "整理文件", "mac 清理", "文件管理工具", "hoolo"],
        "status": "付费",
    },
    {
        "name": "Chupin",
        "url": "https://chupin.site",
        "tagline": "一页纸在线简历，支持 PDF 导出、邮件发送",
        "aliases": ["简历", "在线简历", "简历工具", "求职简历", "resume", "chupin"],
        "status": "付费",
    },
    {
        "name": "Welight",
        "url": "https://welight.fyi",
        "tagline": "公众号创作排版神器，好看的排版，从来简约",
        "aliases": ["公众号排版", "排版工具", "公众号编辑器", "微信排版", "welight"],
        "status": "付费",
    },
    {
        "name": "JevSift",
        "url": "https://jevsift.com",
        "tagline": "投研 Agent 的出处核验 API，对照源文判断声明能不能发",
        "aliases": ["核验", "事实核查", "辟谣", "投研", "出处核验", "jevsift"],
        "status": "付费",
    },
    {
        "name": "鸭小账",
        "url": "https://xuyi.dev",
        "tagline": "现代化、AI 驱动式账本小程序（微信小程序）",
        "aliases": ["记账", "账本", "记账软件", "记账小程序", "鸭小账"],
        "status": "小程序",
    },
    {
        "name": "星潮",
        "url": "https://xingchao.dev",
        "tagline": "自托管的开源 QQ 群助手",
        "aliases": ["qq机器人", "qq 机器人", "群机器人", "qq bot", "星潮", "xingchao"],
        "status": "开源",
    },
    {
        "name": "Ornata",
        "url": "https://ornata.app",
        "tagline": "新概念、轻量化的笔记工具，尽享丝滑",
        "aliases": ["笔记", "笔记软件", "笔记工具", "markdown 笔记", "ornata"],
        "status": "免费",
    },
    {
        "name": "Folio",
        "url": "https://folioedit.dev",
        "tagline": "为读代码而做的 Mac 编辑器",
        "aliases": ["读代码", "代码编辑器", "看代码工具", "folio", "folioedit"],
        "status": "免费",
    },
    {
        "name": "ToolPop",
        "url": "https://toolpop.win",
        "tagline": "功能齐全的在线工具站，无登录，直接用",
        "aliases": ["在线工具", "工具站", "在线小工具", "免费工具站", "toolpop"],
        "status": "免费",
    },
    {
        "name": "Berth",
        "url": "https://berth.fyi",
        "tagline": "Mac 菜单栏端口工具：查看本地端口占用，一键释放",
        "aliases": ["端口", "端口占用", "端口冲突", "释放端口", "lsof", "eaddrinuse", "mac 端口", "berth"],
        "status": "免费开源",
    },
    {
        "name": "词图",
        "url": "https://citu.work",
        "tagline": "社区驱动的 AI 生图提示词精选，永久免费",
        "aliases": ["提示词", "ai 提示词", "生图提示词", "词图", "citu"],
        "status": "免费",
    },
]

# 分类别名：博客分类 → 用户可能使用的说法（补足标题里没出现的主题词）
CATEGORY_ALIASES: dict[str, list[str]] = {
    "ai": ["ai", "人工智能", "大模型", "llm", "ai 应用"],
    "ai-tools": ["ai 工具", "ai工具", "claude", "codex", "cursor", "chatgpt"],
    "Agent开发": ["agent", "智能体", "mcp", "多智能体"],
    "c": ["c语言", "指针", "内存管理"],
    "java": ["java", "jvm", "spring"],
    "linux": ["linux", "shell", "bash"],
    "rust": ["rust", "所有权", "借用检查"],
    "spring-boot": ["spring boot", "springboot", "restful", "接口规范"],
    "ue5": ["ue5", "虚幻引擎", "虚幻"],
    "vibecoding": ["vibe coding", "vibecoding", "ai 编程", "ai编程", "编码代理"],
    "docker": ["docker", "容器", "镜像", "docker compose"],
    "前端技术": ["前端", "javascript", "typescript", "react", "vue", "css"],
    "博客小记": ["博客", "建站", "博客搭建"],
    "图床": ["图床", "图片存储", "图片上传"],
    "开发工具": ["开发工具", "ide", "编辑器", "插件"],
    "效率工具": ["效率工具", "mac 软件", "mac软件", "常用软件"],
    "数据库": ["数据库", "mysql", "redis", "sql"],
    "数据结构与算法": ["算法", "数据结构", "leetcode", "刷题"],
    "日志管理": ["日志", "log", "日志系统", "elk"],
    "游戏开发": ["游戏开发", "unity", "游戏编程", "独立游戏", "独游"],
    "版本控制": ["git", "github", "版本控制", "git 分支", "分支管理", "代码提交", "rebase", "merge", "gitignore"],
    "独立开发": ["独立开发", "独立开发者", "出海", "收款", "支付", "stripe", "dodo", "变现", "副业", "个人开发"],
    "软件": ["软件", "软件工具"],
    "鉴权": ["鉴权", "认证", "jwt", "oauth"],
    "闲事奇闻": [],
}

# ---------------------------------------------------------------- 文本处理

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ASCII_TERM_RE = re.compile(r"[a-z][a-z0-9+#._-]{1,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_FILLER_RE = re.compile(r"[啊呀吧呢吗哦噢嗯诶唉嘛嘞]+")
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

# 停用词：疑问词 / 口语 / 泛词，避免「怎么、如何、兄弟们」这类词误命中
_STOPWORDS = {
    "如何", "怎么", "怎样", "什么", "为什么", "为何", "哪里", "哪个", "哪些", "是否",
    "有没有", "能不能", "可不可以", "请问", "请教", "求助", "求解", "推荐", "一个",
    "一下", "这个", "那个", "这些", "那些", "我们", "你们", "他们", "自己", "现在",
    "今天", "明天", "昨天", "已经", "就是", "不是", "还是", "但是", "因为", "所以",
    "如果", "然后", "感觉", "觉得", "知道", "不会", "不能", "需要", "问题", "东西",
    "时候", "可以", "应该", "可能", "真的", "兄弟们", "兄弟", "大佬", "老哥", "谢谢",
    "不错", "好用", "怎么办", "咋办", "咋整", "有点", "其实", "大家", "各位", "没有",
    "the", "and", "for", "with", "this", "that", "you", "are", "how", "what",
}

# 中文停用词（长词优先）用于切分，避免「是什么/什么东西」这类跨词碎片进入索引
_CJK_STOP_SPLIT_RE = re.compile(
    "|".join(map(re.escape, sorted(
        (w for w in _STOPWORDS if not w.isascii()), key=len, reverse=True
    )))
)


def html_to_text(raw: str) -> str:
    """HTML 片段 → 纯文本（去 script/style/标签、反转义、压缩空白）。"""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _terms(text: str) -> set[str]:
    """提取可匹配词：英文单词（≥2） + 中文 2-4 字 n-gram。

    中文先按语气助词与停用词切分，再生成 n-gram，避免跨词碎片（如“是什么”）。
    """
    lowered = text.lower()
    out: set[str] = set()
    for m in _ASCII_TERM_RE.finditer(lowered):
        word = m.group(0).strip("._+#-")
        if len(word) >= 2 and word not in _STOPWORDS:
            out.add(word)
    for m in _CJK_RUN_RE.finditer(lowered):
        for chunk in _CJK_FILLER_RE.split(m.group(0)):
            for run in _CJK_STOP_SPLIT_RE.split(chunk):
                for n in (2, 3, 4):
                    if len(run) < n:
                        continue
                    for i in range(len(run) - n + 1):
                        term = run[i:i + n]
                        if term not in _STOPWORDS:
                            out.add(term)
    return out


def _term_weight(term: str) -> float:
    if term.isascii():
        return min(len(term), 6) * 0.9
    return len(term) * 1.4


# ---------------------------------------------------------------- Feed 抓取 / 解析


async def fetch_feed(timeout: float = 20.0) -> bytes:
    """抓取 RSS（仅访问固定域名 xuyi.dev）。"""
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as client:
        resp = await client.get(FEED_URL)
        resp.raise_for_status()
        data = resp.content
    if len(data) > MAX_FEED_BYTES:
        raise ValueError(f"feed 体积异常（{len(data)} bytes）")
    return data


def _iso_date(raw: str) -> str:
    """RFC822（RSS pubDate）→ YYYY-MM-DD；解析失败时原样返回。"""
    raw = (raw or "").strip()
    try:
        return parsedate_to_datetime(raw).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return raw


def parse_feed(xml_bytes: bytes) -> list[dict[str, Any]]:
    """解析 RSS，返回文章记录（不含正文全文，只留节选）。"""
    root = ElementTree.fromstring(xml_bytes)
    posts: list[dict[str, Any]] = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        content_el = item.find(_CONTENT_NS)
        body = html_to_text(content_el.text or "") if content_el is not None else ""
        summary = html_to_text(item.findtext("description") or "")
        posts.append({
            "title": title,
            "url": link,
            "summary": summary[:300],
            "category": (item.findtext("category") or "").strip(),
            "published": _iso_date(item.findtext("pubDate") or ""),
            "excerpt": body[:EXCERPT_CHARS],
        })
    return posts


_SITEMAP_POST_RE = re.compile(
    r"<loc>(https://xuyi\.dev/\d{4}-\d{2}-\d{2}[A-Za-z0-9._~!$&'()*+,;=@%\-]*)</loc>"
)
_URL_DATE_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})")

_TITLE_TAG_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_META_DESC_RE = re.compile(
    r'(?is)<meta\s+(?:name|property)="(?:description|og:description)"\s+content="([^"]*)"'
)
_ARTICLE_RE = re.compile(r"(?is)<article[^>]*>(.*?)</article>")
_MAIN_RE = re.compile(r"(?is)<main[^>]*>(.*?)</main>")
_CATEGORY_LINK_RE = re.compile(r'href="/category/([^"/?#]+)"')
_KAMI_LABEL_RE = re.compile(r'class="kami-label">([^<]+?)\s*·\s*文章')
_ESC_TAGS_RE = re.compile(r'\\"tags\\":\[(.*?)\]')
_ESC_TAG_ITEM_RE = re.compile(r'\\"([^\\"]+)\\"')


def parse_sitemap(xml_bytes: bytes) -> list[str]:
    """sitemap → 全部文章 URL（按出现顺序，含较老、不在 RSS 里的文章）。"""
    text = xml_bytes.decode("utf-8", errors="replace")
    seen: set[str] = set()
    urls: list[str] = []
    for m in _SITEMAP_POST_RE.finditer(text):
        url = m.group(1)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _extract_category(raw_html: str) -> str:
    m = _KAMI_LABEL_RE.search(raw_html)
    if m:
        return html.unescape(_WS_RE.sub(" ", m.group(1))).strip()
    m = _CATEGORY_LINK_RE.search(raw_html)
    return unquote(m.group(1)).strip() if m else ""


def _extract_tags(raw_html: str) -> list[str]:
    """文章页内嵌 JSON 中的 tags（如 ["Git","GitHub"]），作为索引强化信号。"""
    m = _ESC_TAGS_RE.search(raw_html)
    if not m:
        return []
    return [html.unescape(t) for t in _ESC_TAG_ITEM_RE.findall(m.group(1))][:12]


def parse_post_page(url: str, raw_html: str) -> dict[str, Any]:
    """解析单篇文章页面：标题 / 摘要 / 正文节选 / 分类 / 标签 / 日期（来自 URL）。"""
    title = ""
    m = _TITLE_TAG_RE.search(raw_html)
    if m:
        title = html.unescape(_WS_RE.sub(" ", m.group(1))).strip()
        title = re.sub(r"\s*[·|｜]\s*XuYi\s*$", "", title).strip()
    summary = ""
    m = _META_DESC_RE.search(raw_html)
    if m:
        summary = html.unescape(_WS_RE.sub(" ", m.group(1))).strip()
    body_match = _ARTICLE_RE.search(raw_html) or _MAIN_RE.search(raw_html)
    excerpt = html_to_text(body_match.group(1))[:EXCERPT_CHARS] if body_match else ""
    date_match = _URL_DATE_RE.search(url)
    return {
        "title": title or url.rsplit("/", 1)[-1],
        "url": url,
        "summary": summary[:300],
        "category": _extract_category(raw_html),
        "tags": _extract_tags(raw_html),
        "published": date_match.group(1) if date_match else "",
        "excerpt": excerpt,
    }


async def fetch_sitemap(timeout: float = 20.0) -> bytes:
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as client:
        resp = await client.get(SITEMAP_URL)
        resp.raise_for_status()
        return resp.content


async def fetch_pages(urls: list[str]) -> dict[str, dict[str, Any]]:
    """并发抓取文章页面（限流 4 并发）；失败的单篇跳过并记录日志。"""
    results: dict[str, dict[str, Any]] = {}
    if not urls:
        return results
    semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=PAGE_TIMEOUT, headers={"User-Agent": USER_AGENT}
    ) as client:
        async def one(url: str) -> None:
            async with semaphore:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    results[url] = parse_post_page(url, resp.text)
                except Exception as e:
                    logger.warning(f"抓取文章页失败 {url}：{e}")
        await asyncio.gather(*(one(url) for url in urls))
    return results


# ---------------------------------------------------------------- 相关性索引


class BlogIndex:
    """倒排索引 + IDF 打分。文章与作品统一召回，按类型分开返回。"""

    def __init__(self, posts: list[dict[str, Any]],
                 products: list[dict[str, Any]] = PRODUCTS) -> None:
        self.posts = list(posts)
        self.products = list(products)
        self._entries: list[dict[str, Any]] = []
        self._postings: dict[str, dict[int, float]] = {}
        self._idf: dict[str, float] = {}
        self._build()

    def _add_terms(self, idx: int, text: str, weight: float) -> None:
        for term in _terms(text):
            bucket = self._postings.setdefault(term, {})
            if weight > bucket.get(idx, 0.0):
                bucket[idx] = weight

    def _build(self) -> None:
        for p in self.posts:
            idx = len(self._entries)
            self._entries.append({
                "kind": "post",
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "summary": p.get("summary", ""),
                "category": p.get("category", ""),
                "published": p.get("published", ""),
                "excerpt": p.get("excerpt", ""),
                "base": 1.0,
            })
            category = p.get("category", "")
            core = " ".join([
                p.get("title", ""),
                category,
                " ".join(CATEGORY_ALIASES.get(category, [])),
                " ".join(p.get("tags", []) or []),
            ])
            self._add_terms(idx, core, 1.0)
            self._add_terms(idx, p.get("excerpt", ""), 0.35)
        for product in self.products:
            idx = len(self._entries)
            self._entries.append({
                "kind": "product",
                "title": product.get("name", ""),
                "url": product.get("url", ""),
                "summary": product.get("tagline", ""),
                "category": "作品集",
                "published": "",
                "excerpt": "",
                "base": 1.15,
            })
            core = " ".join([
                product.get("name", ""),
                product.get("tagline", ""),
                " ".join(product.get("aliases", [])),
            ])
            self._add_terms(idx, core, 1.0)
        total = max(1, len(self._entries))
        self._idf = {
            term: math.log(1 + total / len(bucket))
            for term, bucket in self._postings.items()
        }

    def _rank(self, text: str) -> list[tuple[int, float]]:
        if not text:
            return []
        scores: dict[int, float] = {}
        for term in _terms(text):
            bucket = self._postings.get(term)
            if not bucket:
                continue
            w = _term_weight(term) * self._idf[term]
            for idx, entry_weight in bucket.items():
                base = self._entries[idx]["base"]
                scores[idx] = scores.get(idx, 0.0) + w * entry_weight * base
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    @staticmethod
    def _hit(entry: dict[str, Any], score: float) -> dict[str, Any]:
        return {
            "kind": entry["kind"],
            "title": entry["title"],
            "url": entry["url"],
            "summary": entry["summary"],
            "category": entry["category"],
            "published": entry["published"],
            "score": round(score, 2),
        }

    def recall(self, text: str, max_posts: int = 3,
               max_products: int = 2) -> dict[str, Any] | None:
        """消息 → 相关文章/作品；无命中返回 None。

        作品额外要求与最佳作品得分同量级（相对阈值），避免泛词带出无关产品。
        """
        ranked = self._rank(text)
        if not ranked:
            return None
        best_product = max(
            (s for i, s in ranked if self._entries[i]["kind"] == "product"),
            default=0.0,
        )
        product_threshold = max(MIN_PRODUCT_SCORE, best_product * 0.4)
        posts: list[dict[str, Any]] = []
        products: list[dict[str, Any]] = []
        for idx, score in ranked:
            entry = self._entries[idx]
            if entry["kind"] == "product":
                if len(products) >= max_products or score < product_threshold:
                    continue
                products.append(self._hit(entry, score))
            else:
                if score < MIN_SCORE:
                    break
                if len(posts) < max_posts:
                    posts.append(self._hit(entry, score))
            if len(posts) >= max_posts and len(products) >= max_products:
                break
        if not posts and not products:
            return None
        top_score = ranked[0][1]
        return {
            "posts": posts,
            "products": products,
            "top_score": round(top_score, 2),
            "strong": top_score >= STRONG_SCORE,
        }

    def search(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        """工具查询：返回综合排序的相关条目。"""
        ranked = self._rank(text)
        hits: list[dict[str, Any]] = []
        for idx, score in ranked:
            entry = self._entries[idx]
            threshold = MIN_PRODUCT_SCORE if entry["kind"] == "product" else MIN_SCORE
            if score < threshold or len(hits) >= limit:
                break
            hits.append(self._hit(entry, score))
        return hits

    def get_post(self, url_or_title: str) -> dict[str, Any] | None:
        """按链接或标题关键词查找文章（用于 get_blog_post 工具）。"""
        query = (url_or_title or "").strip()
        if not query:
            return None
        for p in self.posts:
            if p.get("url") == query:
                return p
        lowered = query.lower()
        for p in self.posts:
            if lowered in (p.get("title") or "").lower():
                return p
        return None
