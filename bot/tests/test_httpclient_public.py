"""只连公网的客户端：在建立 TCP 连接时检查 IP（防 DNS 重绑定）。"""

import asyncio
import socket

import httpx
import pytest

from src import httpclient


@pytest.fixture
async def local_server():
    """本机 127.0.0.1 上的一个 HTTP 服务，记录是否被连上过。"""
    hits = []

    async def handle(reader, writer):
        hits.append(True)
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, hits
    server.close()
    await server.wait_closed()


@pytest.fixture(autouse=True)
async def _fresh_clients():
    await httpclient.close_http()
    yield
    await httpclient.close_http()


async def test_plain_client_reaches_localhost(local_server, monkeypatch):
    """对照组：普通共享客户端能连上本机服务，说明下面的拦截来自 public 客户端本身。"""
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    port, hits = local_server
    r = await httpclient.get_http().get(f"http://127.0.0.1:{port}/", timeout=5)
    assert r.status_code == 200 and hits


async def test_public_client_blocks_loopback_literal(local_server):
    port, hits = local_server
    with pytest.raises(httpx.ConnectError, match="内网"):
        await httpclient.get_public_http().get(f"http://127.0.0.1:{port}/", timeout=5)
    assert not hits


async def test_public_client_blocks_rebound_hostname(local_server, monkeypatch):
    """域名在连接时解析到 127.0.0.1（DNS 重绑定）：连接层拒绝，服务从未被连上。"""
    port, hits = local_server
    real = socket.getaddrinfo

    def fake(host, *a, **kw):
        if host == "rebind.example":
            return real("127.0.0.1", *a, **kw)
        return real(host, *a, **kw)

    monkeypatch.setattr(httpclient.socket, "getaddrinfo", fake)
    with pytest.raises(httpx.ConnectError, match="内网"):
        await httpclient.get_public_http().get(f"http://rebind.example:{port}/", timeout=5)
    assert not hits


async def test_connects_to_the_checked_ip(monkeypatch):
    """检查通过后直接连检查过的 IP，而不是把域名交给下层再解析一次。"""
    monkeypatch.setattr(
        httpclient.socket, "getaddrinfo",
        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )
    backend = httpclient._PublicOnlyBackend()
    seen = []

    async def fake_connect(host, port, **kw):
        seen.append((host, port))
        return object()

    monkeypatch.setattr(backend._inner, "connect_tcp", fake_connect)
    await backend.connect_tcp("example.com", 443)
    assert seen == [("93.184.216.34", 443)]


async def test_mixed_public_and_private_answers_blocked(monkeypatch):
    """DNS 同时返回公网和内网地址也拒绝（不能赌连到哪一个）。"""
    monkeypatch.setattr(
        httpclient.socket, "getaddrinfo",
        lambda host, port, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port)),
        ],
    )
    with pytest.raises(httpclient.BlockedAddressError):
        await httpclient._PublicOnlyBackend().connect_tcp("example.com", 80)


def test_public_client_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.9:8080")
    assert httpclient.get_public_http()._trust_env is False


@pytest.mark.parametrize("ip,ok", [
    ("8.8.8.8", True), ("127.0.0.1", False), ("10.1.2.3", False), ("169.254.169.254", False),
    ("::1", False), ("::ffff:127.0.0.1", False), ("0.0.0.0", False), ("not-an-ip", False),
])
def test_is_public_ip(ip, ok):
    assert httpclient.is_public_ip(ip) is ok
