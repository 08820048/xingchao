"""SQLite 存储层（aiosqlite）。只存 kv 与统计，禁止写聊天内容。

按领域拆成 mixin，组合成单一的 Store（所有插件仍只用 get_store()）：
- base       连接、WAL、kv 写穿透缓存
- schema     建表与版本化迁移（改表结构只往 _MIGRATIONS 末尾追加）
- stats      发言计数、工作台账计数
- events     QQ 登录 / 掉线事件
- schedules  定时消息、订阅推送、个人提醒
- memory     AI 长期记忆
- moderation 处罚、申诉、敏感词统计
- content    博客索引、群知识库
- stickers   群友存下的表情包
"""

from __future__ import annotations

from typing import Optional

from nonebot import get_driver

from src.config import get_config
from src.store.base import StoreBase
from src.store.content import ContentMixin
from src.store.events import EventsMixin
from src.store.memory import MemoryMixin
from src.store.moderation import ModerationMixin
from src.store.schedules import SchedulesMixin
from src.store.schema import _MIGRATIONS, _SCHEMA
from src.store.stats import StatsMixin
from src.store.stickers import StickersMixin

__all__ = ["Store", "StoreBase", "get_store", "_MIGRATIONS", "_SCHEMA"]


class Store(
    StatsMixin, EventsMixin, SchedulesMixin, MemoryMixin, ModerationMixin, ContentMixin,
    StickersMixin, StoreBase,
):
    """全部领域能力的组合；实际状态（连接、锁、kv 缓存）都在 StoreBase。"""


_store: Optional[Store] = None
_shutdown_hooked: bool = False


def get_store() -> Store:
    """惰性单例：首次调用需在 nonebot.init() 之后。"""
    global _store, _shutdown_hooked
    if _store is None:
        _store = Store(get_config().xingchao_db_path)
    if not _shutdown_hooked:
        get_driver().on_shutdown(_store.close)
        _shutdown_hooked = True
    return _store
