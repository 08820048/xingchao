"""AI 工具注册表：@tool 的约束与 TOOL_ORDER 一致性。"""

import pytest

from src.aitools import TOOL_ORDER, build_tools
from src.aitools.registry import _REGISTRY, tool


def test_order_matches_registry():
    tools = build_tools()
    assert [s["function"]["name"] for s, _ in tools] == list(TOOL_ORDER)
    assert len(tools) == len(_REGISTRY)
    assert all(perm in ("all", "superuser") for _, (perm, _h) in tools)


def test_build_tools_returns_copy():
    a = build_tools()
    a.clear()
    assert build_tools()


def test_duplicate_name_rejected():
    name = TOOL_ORDER[0]
    with pytest.raises(ValueError, match="重名"):
        tool(name, "x", {"type": "object", "properties": {}}, "all")(lambda *a: None)


def test_bad_perm_rejected():
    with pytest.raises(ValueError, match="perm"):
        tool("__never_registered__", "x", {}, "admin")
