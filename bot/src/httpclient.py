"""进程级共享的 httpx.AsyncClient：复用连接池，避免每次请求都重新 DNS + TCP + TLS 握手。

群图片审核、语音下载每条消息都要拉 QQ 图床，链接解读、订阅推送也频繁访问同几个站点，
共享客户端能让这些请求走 keep-alive。headers / timeout 按请求传入：

    resp = await get_http().get(url, headers={"User-Agent": UA}, timeout=15)

一次性要发很多请求、需要自定义并发的场景（博客全站抓取、聚合搜索）仍可自建 client。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore
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
    global _client, _public_client
    for c in (_client, _public_client):
        if c is not None:
            await c.aclose()
    _client = _public_client = None


# ---------------------------------------------------------------- 只连公网的客户端
# 给「抓群友发来的任意网址」用（链接解读）。只在发请求前检查 URL 的域名还不够：
# 检查时解析一次 DNS，真正连接时 httpx 会再解析一次，攻击者控制的 DNS 可以第一次给公网 IP、
# 第二次给 127.0.0.1（DNS 重绑定）。这里在建立 TCP 连接的那一刻自己解析、检查、
# 并直接连到检查过的 IP；TLS 的 SNI / 证书校验仍用原域名（httpcore 取自请求的 origin）。


def is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


class BlockedAddressError(httpcore.ConnectError):
    """目标解析到内网 / 保留地址，拒绝连接。"""


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise httpcore.ConnectError(f"解析失败：{host}") from e
        ips = list(dict.fromkeys(info[4][0] for info in infos))
        if not ips or not all(is_public_ip(ip) for ip in ips):
            raise BlockedAddressError(f"拒绝连接内网/保留地址：{host} → {ips}")
        return await self._inner.connect_tcp(
            ips[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise BlockedAddressError("不允许 unix socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


_public_client: httpx.AsyncClient | None = None


def get_public_http() -> httpx.AsyncClient:
    """只会连到公网 IP 的共享客户端；不读代理环境变量（走代理时 DNS 在代理端解析，检查会失效）。
    不自动跟随跳转：调用方应逐跳处理，每一跳都会再经过连接层的检查。"""
    global _public_client, _shutdown_hooked
    if _public_client is None or _public_client.is_closed:
        transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5, keepalive_expiry=30),
        )
        # httpx 没有公开 network_backend 参数；连接池的这个属性自 httpcore 1.0 起稳定，
        # tests/test_httpclient_public.py 会在升级后它失效时报错
        transport._pool._network_backend = _PublicOnlyBackend()
        _public_client = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=DEFAULT_TIMEOUT,
        )
    if not _shutdown_hooked:
        get_driver().on_shutdown(close_http)
        _shutdown_hooked = True
    return _public_client
