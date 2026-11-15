"""联网搜索：多源降级链、DeepSeek 官方搜索、计数与缓存、原生搜索开关。"""

from types import SimpleNamespace

import pytest

from src.plugins import ai as ai_plugin
from src.plugins import webtools


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    # webtools 内部是 `from src.store import get_store`（调用时导入），
    # 所以必须 patch 源模块，patch webtools.get_store 是无效的
    import src.store as store_mod

    monkeypatch.setattr(store_mod, "get_store", lambda: store)
    webtools._reset_state()
    monkeypatch.setattr(webtools, "deepseek_creds", _fake_creds)
    yield
    webtools._reset_state()


async def _fake_creds():
    return "https://api.deepseek.com", "sk-test"


def _patch_cfg(monkeypatch, **over):
    """每个用例一份独立的环境配置，避免互相污染。"""
    cfg = SimpleNamespace(
        xingchao_search_provider="deepseek",
        xingchao_search_providers="",
        xingchao_search_api_key="",
        xingchao_search_url="",
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    monkeypatch.setattr(webtools, "get_config", lambda: cfg)
    return cfg


# ---------------------------------------------------------------- 链解析

def test_parse_chain_accepts_many_shapes():
    assert webtools._parse_chain("") == []
    assert webtools._parse_chain("deepseek") == ["deepseek"]
    assert webtools._parse_chain("deepseek,duckduckgo") == ["deepseek", "duckduckgo"]
    assert webtools._parse_chain('["brave","searxng"]') == ["brave", "searxng"]
    assert webtools._parse_chain("deepseek, NOPE ,deepseek") == ["deepseek"]  # 去重 + 丢弃未知源
    assert webtools._parse_chain(["duckduckgo", "searxng"]) == ["duckduckgo", "searxng"]


async def test_chain_priority_kv_over_env(monkeypatch, store):
    _patch_cfg(monkeypatch)
    assert await webtools.search_chain() == ["deepseek"]  # 单源 env

    _patch_cfg(monkeypatch, xingchao_search_providers="searxng,brave")
    assert await webtools.search_chain() == ["searxng", "brave"]  # 多源 env

    await store.set_kv("search_providers", '["duckduckgo"]')
    assert await webtools.search_chain() == ["duckduckgo"]  # 面板 kv 最优先

    _patch_cfg(monkeypatch, xingchao_search_provider="")
    await store.set_kv("search_providers", "")
    assert await webtools.search_chain() == list(webtools.DEFAULT_CHAIN)


# ---------------------------------------------------------------- 降级

async def test_falls_through_to_next_provider(monkeypatch, store):
    """第一个源失败 → 自动换下一个，而不是整体失败。"""
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek","duckduckgo"]')
    calls = []

    async def boom(name, q):
        calls.append(name)
        if name == "deepseek":
            raise RuntimeError("down")
        return [{"title": "T", "url": "https://a.com", "snippet": "S"}]

    monkeypatch.setattr(webtools, "_run_provider", boom)
    res, note = await webtools.search("python")
    assert calls == ["deepseek", "duckduckgo"]
    assert note == "" and res and res[0]["url"] == "https://a.com"


async def test_all_fail_gives_friendly_note_without_internals(monkeypatch, store):
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek","duckduckgo"]')

    async def boom(name, q):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(webtools, "_run_provider", boom)
    res, note = await webtools.search("python")
    assert res is None
    assert "暂时不可用" in note and "面板" in note
    assert "401" not in note and "XINGCHAO" not in note  # 不把内部细节甩给群友


async def test_empty_results_from_all_sources_is_not_an_error(monkeypatch, store):
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek"]')

    async def empty(name, q):
        return []

    monkeypatch.setattr(webtools, "_run_provider", empty)
    res, note = await webtools.search("asdkjhasd")
    assert res == [] and note == ""
    assert "没搜到" in await webtools.search_text("asdkjhasd")


# ---------------------------------------------------------------- 计数与缓存

async def test_counter_only_counts_success(monkeypatch, store):
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek"]')

    async def boom(name, q):
        raise RuntimeError("down")

    monkeypatch.setattr(webtools, "_run_provider", boom)
    for _ in range(3):
        await webtools.search(f"q{_}")
    assert await webtools._used_today() == 0  # 失败不吃额度

    async def ok(name, q):
        return [{"title": "T", "url": "https://a.com", "snippet": "S"}]

    monkeypatch.setattr(webtools, "_run_provider", ok)
    await webtools.search("q-ok")
    assert await webtools._used_today() == 1


async def test_daily_limit_blocks_further_searches(monkeypatch, store):
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek"]')
    await store.set_kv("search_daily_limit", "1")

    async def ok(name, q):
        return [{"title": "T", "url": "https://a.com", "snippet": "S"}]

    monkeypatch.setattr(webtools, "_run_provider", ok)
    assert (await webtools.search("one"))[0]
    res, note = await webtools.search("two")
    assert res is None and "用完" in note


async def test_cache_reuses_result_and_skips_network(monkeypatch, store):
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek"]')
    calls = []

    async def ok(name, q):
        calls.append(q)
        return [{"title": "T", "url": "https://a.com", "snippet": "S"}]

    monkeypatch.setattr(webtools, "_run_provider", ok)
    await webtools.search("python")
    await webtools.search("python")
    assert calls == ["python"]  # 第二次命中缓存
    assert await webtools._used_today() == 1  # 缓存命中不额外计数

    await store.set_kv("search_cache_ttl", "0")
    await webtools.search("python")
    assert calls == ["python", "python"]  # 关掉缓存后重新请求


# ---------------------------------------------------------------- DeepSeek 源

async def test_deepseek_provider_parses_structured_results(monkeypatch, store):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"title": "DeepSeek V4 发布", "url": "https://example.com/a",
                 "snippet": "短摘要", "summary": "长摘要" * 3,
                 "site_name": "Example", "date": "2026-06-01T00:00:00+00:00"},
                {"title": "无链接", "url": "not-a-url"},
            ]}

    seen = {}

    class Client:
        async def post(self, url, headers=None, json=None):
            seen["url"], seen["headers"], seen["json"] = url, headers, json
            return Resp()

    res = await webtools._search_deepseek(Client(), "DeepSeek V4")
    assert seen["url"] == "https://api.deepseek.com/v1/tools/search"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["json"]["count"] == webtools.MAX_RESULTS
    assert len(res) == 1  # 无合法链接的被丢弃
    assert res[0]["summary"].startswith("长摘要") and res[0]["site"] == "Example"
    text = webtools.format_results(res)
    assert "长摘要" in text and "2026-06-01" in text and "https://example.com/a" in text


def test_provider_status_marks_missing_keys(monkeypatch):
    _patch_cfg(monkeypatch)
    import asyncio

    status = asyncio.run(webtools.provider_status())
    by_key = {s["key"]: s for s in status}
    assert by_key["deepseek"]["ready"] is True  # 假凭据
    assert by_key["brave"]["ready"] is False and "API_KEY" in by_key["brave"]["note"]
    assert by_key["searxng"]["ready"] is False and "SEARCH_URL" in by_key["searxng"]["note"]


# ---------------------------------------------------------------- 原生搜索

def _cfg(**over):
    base = dict(ai_plugin.DEFAULTS)
    base.update(over)
    return base


async def test_native_search_needs_deepseek_model_and_endpoint(monkeypatch):
    monkeypatch.setattr(ai_plugin, "_native_search_supported", True)

    async def creds():
        return "https://api.deepseek.com", "sk-x"

    monkeypatch.setattr(ai_plugin, "_ai_creds", creds)
    assert await ai_plugin.native_search_ready(_cfg(ai_model="deepseek-flash")) is True
    # 关掉开关就不声明
    assert await ai_plugin.native_search_ready(_cfg(ai_model="deepseek-flash", ai_native_search=False)) is False
    # 非 deepseek 模型
    assert await ai_plugin.native_search_ready(_cfg(ai_model="glm-4-flash")) is False

    async def other_base():
        return "https://api.b.ai/v1", "sk-x"

    monkeypatch.setattr(ai_plugin, "_ai_creds", other_base)
    assert await ai_plugin.native_search_ready(_cfg(ai_model="deepseek-flash")) is False  # 中转站不支持


async def test_native_search_served_then_disabled_after_failure(monkeypatch):
    monkeypatch.setattr(ai_plugin, "_native_search_supported", True)

    async def creds():
        return "https://api.deepseek.com", "sk-x"

    monkeypatch.setattr(ai_plugin, "_ai_creds", creds)
    assert await ai_plugin.native_search_ready(_cfg(ai_model="deepseek-flash")) is True
    ai_plugin._native_search_supported = False  # 模拟被服务端拒绝一次
    assert await ai_plugin.native_search_ready(_cfg(ai_model="deepseek-flash")) is False
    monkeypatch.setattr(ai_plugin, "_native_search_supported", True)


def test_drop_native_tool_keeps_function_tools():
    tools = [{"type": "deepseek_web_search"}, {"type": "function", "function": {"name": "f"}}]
    assert ai_plugin._drop_native_tool(tools) == [{"type": "function", "function": {"name": "f"}}]
    assert ai_plugin._drop_native_tool([{"type": "deepseek_web_search"}]) is None


def test_capability_prompt_warns_about_fake_search_results():
    text = ai_plugin._capability_prompt(False)
    assert "联网搜索失败" in text and "绝不能用记忆里的旧信息" in text


def test_log_citations_does_not_raise():
    msg = SimpleNamespace(annotations=[{"type": "url_citation", "url_citation": {"url": "https://a.com"}},
                                       {"type": "url_citation"}])
    ai_plugin._log_citations(msg)  # 不抛异常即可
    ai_plugin._log_citations(SimpleNamespace(annotations=None))


async def test_one_source_empty_and_another_failing_is_not_an_error(monkeypatch, store):
    """DeepSeek 正常返回空 + DuckDuckGo 被拦截 → 应判定「没搜到」，而不是服务故障。"""
    _patch_cfg(monkeypatch)
    await store.set_kv("search_providers", '["deepseek","duckduckgo"]')

    async def mixed(name, q):
        if name == "deepseek":
            return []
        raise webtools.SearchBlocked("blocked")

    monkeypatch.setattr(webtools, "_run_provider", mixed)
    res, note = await webtools.search("asdkjhasd")
    assert res == [] and note == ""
