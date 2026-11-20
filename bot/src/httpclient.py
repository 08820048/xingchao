"""进程级共享的 httpx.AsyncClient：复用连接池，避免每次请求都重新 DNS + TCP + TLS 握手。

群图片审核、语音下载每条消息都要拉 QQ 图床，链接解读、订阅推送也频繁访问同几个站点，
共享客户端能让这些请求走 keep-alive。headers / timeout 按请求传入：

    resp = await get_http().get(url, headers={"User-Agent": UA}, timeout=15)

一次性要发很多请求、需要自定义并发的场景（博客全站抓取、聚合搜索）仍可自建 client。
"""

from __future__ import annotations

import httpx
from nonebot import get_driver

DEFAULT_TIMEOUT = 15.0

_client: httpx.AsyncClient | None = None
_shutdown_hooked = False


def get_http() -> httpx.AsyncClient:
    global _client, _shutdown_hooked
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10, keepalive_expiry=30),
        )
    if not _shutdown_hooked:
        get_driver().on_shutdown(close_http)
        _shutdown_hooked = True
    return _client


async def close_http() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
