"""后台周期任务的统一入口：替代各插件里重复的 `_worker_started + create_task + while True` 样板。

用法（在插件里）：

    @periodic("提醒扫描", 20)
    async def _scan(bot: Bot) -> None:
        await tick(bot)

- 机器人启动后自动开始；每 `seconds` 秒调用一次，等到有 bot 连上才执行
- 单次异常只记日志，不会中断循环
- 任务句柄保存在模块级集合里，避免被垃圾回收（asyncio 只对任务保持弱引用）
- 同名任务重复注册（插件被重复导入）只启动一次
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nonebot import get_bots, get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

_tasks: dict[str, asyncio.Task] = {}


def first_bot() -> Bot | None:
    return next((b for b in get_bots().values() if isinstance(b, Bot)), None)


def periodic(name: str, seconds: float, *, wait_bot: bool = True, run_first: bool = False):
    """把 `async def fn(bot)` 注册为周期任务。

    wait_bot=False：没有 bot 连接也执行（传入 None）；run_first=True：启动后先执行一次再开始等待。
    """

    def deco(fn: Callable[[Bot], Awaitable[object]]):
        async def loop() -> None:
            first = run_first
            while True:
                if not first:
                    await asyncio.sleep(seconds)
                first = False
                try:
                    bot = first_bot()
                    if bot is not None or not wait_bot:
                        await fn(bot)  # type: ignore[arg-type]
                except Exception:
                    logger.exception(f"{name}异常")

        @get_driver().on_startup
        async def _start() -> None:
            if name not in _tasks:
                _tasks[name] = asyncio.get_running_loop().create_task(loop(), name=name)

        return fn

    return deco
