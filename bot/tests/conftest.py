"""测试环境：初始化一个最小的 NoneBot（不连 NapCat），让插件模块可以被导入。"""

import nonebot
import pytest

nonebot.init(superusers={"1", "2"})


@pytest.fixture
async def store(tmp_path):
    """每个用例一个独立的 SQLite。"""
    from src.store import Store

    s = Store(tmp_path / "t.db")
    yield s
    await s.close()  # 新版 aiosqlite 的线程不是 daemon，不关会让 pytest 进程无法退出


@pytest.fixture(autouse=True)
async def _close_global_store():
    """有的用例会间接用到全局 get_store() 单例；用例结束时在同一个事件循环里关掉它，
    否则 aiosqlite 的工作线程会让 pytest 进程在测试通过后无法退出（CI 会一直挂着）。"""
    yield
    import src.store as store_mod

    s = store_mod._store
    if s is not None:
        await s.close()
        store_mod._store = None
