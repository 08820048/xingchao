"""链接解读：每一跳跳转都要重新做内网检查（防止公网短链 302 到内网）。"""

import httpx
import pytest

from src.plugins import linkpreview

PAGE = b"<html><head><title>Hello</title></head><body>world</body></html>"
PRIVATE_HOSTS = {"127.0.0.1", "10.0.0.5", "internal.local"}


@pytest.fixture
def fetched(monkeypatch):
    """路由表：host+path → 响应；记录实际被请求过的 URL。"""
    seen: list[str] = []
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        key = f"{request.url.host}{request.url.path}"
        return routes.get(key, httpx.Response(404))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(linkpreview, "get_public_http", lambda: client)

    async def host_allowed(host: str) -> bool:
        return host not in PRIVATE_HOSTS

    monkeypatch.setattr(linkpreview, "_host_allowed", host_allowed)
    return routes, seen


def _html():
    return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE)


def _redirect(location: str, code: int = 302):
    return httpx.Response(code, headers={"location": location})


async def test_redirect_to_private_host_blocked(fetched):
    routes, seen = fetched
    routes["short.example/x"] = _redirect("http://127.0.0.1:8080/admin")
    routes["127.0.0.1/admin"] = _html()
    assert await linkpreview._fetch_page_info("https://short.example/x") is None
    assert seen == ["https://short.example/x"]  # 内网地址从未被请求


async def test_relative_redirect_chain_followed(fetched):
    routes, seen = fetched
    routes["a.example/1"] = _redirect("/2", 301)
    routes["a.example/2"] = _redirect("https://b.example/page")
    routes["b.example/page"] = _html()
    info = await linkpreview._fetch_page_info("https://a.example/1")
    assert info and info["title"] == "Hello"
    assert seen[-1] == "https://b.example/page"


async def test_redirect_to_non_http_scheme_blocked(fetched):
    routes, _ = fetched
    routes["a.example/1"] = _redirect("file:///etc/passwd")
    assert await linkpreview._fetch_page_info("https://a.example/1") is None


async def test_too_many_redirects(fetched):
    routes, seen = fetched
    routes["loop.example/"] = _redirect("https://loop.example/")
    assert await linkpreview._fetch_page_info("https://loop.example/") is None
    assert len(seen) == linkpreview._MAX_REDIRECTS + 1


async def test_private_start_url_blocked(fetched):
    _, seen = fetched
    assert await linkpreview._fetch_page_info("http://10.0.0.5/") is None
    assert seen == []
