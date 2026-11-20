"""工具注册表：@tool 把 OpenAI function 定义和执行器写在一起。

    @tool("get_weather", "查询天气", {"type": "object", ...}, "all")
    async def _t_weather(bot, event, args) -> str: ...

perm：all=所有人 / superuser=仅超管；执行器返回给 LLM 的中文结果摘要。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[Any, Any, dict], Awaitable[str]]
ToolEntry = tuple[dict, tuple[str, Handler]]

_REGISTRY: dict[str, ToolEntry] = {}


def tool(name: str, description: str, params: dict, perm: str):
    """登记一个工具。重名直接报错，避免两个模块悄悄覆盖同一个工具。"""
    if perm not in ("all", "superuser"):
        raise ValueError(f"工具 {name} 的 perm 只能是 all / superuser：{perm!r}")

    def deco(fn: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"工具重名：{name}")
        schema = {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": params},
        }
        _REGISTRY[name] = (schema, (perm, fn))
        return fn

    return deco


def _ai():
    """延迟导入 ai 插件（它反过来依赖本包，直接 import 会循环）。"""
    from src.plugins import ai

    return ai


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
