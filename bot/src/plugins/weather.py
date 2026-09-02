"""天气查询：/天气 <城市>（和风天气 QWeather，纯文本输出，无浏览器依赖）。

- 背景：原计划引入 nonebot-plugin-heweather，但其依赖 chromium 渲染图片，
  内存峰值 150~300MB，1.4G 内存服务器有 OOM 风险，故按规约自写文本版。
- API：QWeather v7 实时天气 + GeoAPI 城市查询，JWT（Ed25519/EdDSA）鉴权。
- 配置（.env / 环境变量，缺一则 /天气 提示配置方法）：
    QWEATHER_JWT_SUB          项目ID（console.qweather.com 项目管理）
    QWEATHER_JWT_KID          JWT Key ID（上传公钥后获取）
    QWEATHER_JWT_PRIVATE_KEY  Ed25519 私钥：支持 base64 或带 \\n 转义的 PEM
    QWEATHER_API_HOST         默认 https://api.qweather.com（免费订阅）
- 同步：AI 工具 get_weather 可自然语言查询（见 docs/AI_CAPABILITIES.md）。
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from nonebot import get_driver, on_command
from nonebot.adapters import Message
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import BASIC

_GEO_HOST = "https://geoapi.qweather.com"
_TOKEN_TTL = 25 * 60  # QWeather 要求 JWT 有效期 ≤ 24h，缓存 25 分钟

_token_cache: tuple[float, str] | None = None


def _private_key_pem() -> str | None:
    cfg = get_driver().config
    raw = (getattr(cfg, "qweather_jwt_private_key", None) or "").strip()
    if not raw:
        return None
    if "BEGIN" in raw:
        return raw.replace("\\n", "\n")
    try:  # base64(PEM)
        return base64.b64decode(raw).decode()
    except Exception:
        return raw.replace("\\n", "\n")


def is_configured() -> bool:
    cfg = get_driver().config
    return bool(
        getattr(cfg, "qweather_jwt_sub", None)
        and getattr(cfg, "qweather_jwt_kid", None)
        and _private_key_pem()
    )


def _missing_config() -> str:
    return (
        "天气功能未配置。请到 https://console.qweather.com 创建项目并上传 Ed25519 公钥，"
        "然后在部署环境设置 QWEATHER_JWT_SUB / QWEATHER_JWT_KID / "
        "QWEATHER_JWT_PRIVATE_KEY（私钥支持 base64 编码）后重启。"
    )


def _get_token() -> str:
    global _token_cache
    if _token_cache and time.time() - _token_cache[0] < _TOKEN_TTL:
        return _token_cache[1]
    import jwt

    cfg = get_driver().config
    now = int(time.time())
    token = jwt.encode(
        {"sub": getattr(cfg, "qweather_jwt_sub"), "iat": now, "exp": now + _TOKEN_TTL + 300},
        _private_key_pem(),
        algorithm="EdDSA",
        headers={"kid": getattr(cfg, "qweather_jwt_kid")},
    )
    _token_cache = (now, token)
    return token


def _host() -> str:
    return (getattr(get_driver().config, "qweather_api_host", None)
            or "https://api.qweather.com").rstrip("/")


async def _get(url: str, **params: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {_get_token()}"})
        r.raise_for_status()
        return r.json()


async def fetch_weather(city: str) -> tuple[dict[str, Any] | None, str]:
    """查询实时天气。返回 (数据, 错误信息)；成功时 err 为空。"""
    if not city:
        return None, "错误：需要 city（城市名）。"
    if not is_configured():
        return None, _missing_config()
    try:
        geo = await _get(f"{_GEO_HOST}/v2/city/lookup", location=city, number=1)
        if geo.get("code") != "200" or not geo.get("location"):
            return None, f"没有找到城市「{city}」（code={geo.get('code')}）"
        loc = geo["location"][0]
        now = await _get(f"{_host()}/v7/weather/now", location=loc["id"])
        if now.get("code") != "200":
            return None, f"天气查询失败（code={now.get('code')}）"
        data = {
            "城市": f"{loc.get('adm2', '')}{loc.get('name', '')}",
            "天气": now["now"].get("text"),
            "温度": f"{now['now'].get('temp')}℃",
            "体感温度": f"{now['now'].get('feelsLike')}℃",
            "风": f"{now['now'].get('windDir')} {now['now'].get('windScale')}级",
            "湿度": f"{now['now'].get('humidity')}%",
            "更新时间": now.get("updateTime", "")[:16].replace("T", " "),
        }
        return data, ""
    except Exception as e:
        logger.warning(f"天气查询失败：{e!r}")
        return None, f"天气查询失败：{e}"


weather_cmd = on_command("天气", rule=BASIC, priority=5, block=True)


async def _send(matcher: Matcher, text: str) -> None:
    try:
        await matcher.send(text)
    except MatcherException:
        raise
    except Exception:
        logger.exception("天气回复发送失败")


@weather_cmd.handle()
async def handle_weather(matcher: Matcher, args: Message = CommandArg()) -> None:
    city = args.extract_plain_text().strip()
    if not city:
        await _send(matcher, "用法：/天气 <城市名>，如 /天气 北京")
        return
    data, err = await fetch_weather(city)
    if err:
        await _send(matcher, err)
        return
    d = data or {}
    await _send(
        matcher,
        f"🌤 {d.get('城市')} 实时天气\n"
        f"天气：{d.get('天气')}，{d.get('温度')}（体感 {d.get('体感温度')}）\n"
        f"风力：{d.get('风')}\n湿度：{d.get('湿度')}\n"
        f"更新：{d.get('更新时间')}",
    )
