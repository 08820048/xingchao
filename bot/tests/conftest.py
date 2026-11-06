"""测试环境：初始化一个最小的 NoneBot（不连 NapCat），让插件模块可以被导入。"""

import nonebot
import pytest

nonebot.init(superusers={"1", "2"})


@pytest.fixture
async def store(tmp_path):
    """每个用例一个独立的 SQLite。"""
    from src.store import Store

    return Store(tmp_path / "t.db")
