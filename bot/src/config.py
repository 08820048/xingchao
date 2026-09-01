"""星潮配置：用 pydantic 从 NoneBot 环境变量读取。"""

from __future__ import annotations

from pathlib import Path

from nonebot import get_driver
from nonebot.log import logger
from pydantic import BaseModel, ConfigDict, field_validator


def _parse_int_set(value: object) -> object:
    """兼容 '111,222' 字符串、单个 int（NoneBot 会把纯数字环境变量解析为 JSON）、列表、集合。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return {value}
    if isinstance(value, str):
        return {int(part) for part in value.replace("，", ",").split(",") if part.strip()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {int(v) for v in value}
    return value


class Config(BaseModel):
    """XINGCHAO_* 环境变量集合。"""

    model_config = ConfigDict(extra="ignore")

    xingchao_superusers: set[int] = set()
    xingchao_group_whitelist: set[int] = set()
    xingchao_log_dir: Path = Path("./data/logs")
    xingchao_db_path: Path = Path("./data/xingchao.db")
    xingchao_replies_path: Path = Path("./data/replies.json")
    xingchao_reply_cooldown: int = 8
    xingchao_site: str = "https://xingchao.dev"

    @field_validator(
        "xingchao_superusers",
        "xingchao_group_whitelist",
        mode="before",
    )
    @classmethod
    def _ids(cls, v: object) -> object:
        return _parse_int_set(v)


_config: Config | None = None


def get_config() -> Config:
    """惰性单例：首次调用需在 nonebot.init() 之后。"""
    global _config
    if _config is None:
        _config = Config.model_validate(get_driver().config.model_dump())
        logger.debug(f"星潮配置加载完成：白名单群 {_config.xingchao_group_whitelist or '{空}'}")
    return _config


def superuser_ids() -> set[int]:
    """超管 QQ 号集合（nonebot SUPERUSERS，int 化）。"""
    return {int(uid) for uid in get_driver().config.superusers}


def sync_superusers() -> None:
    """用 XINGCHAO_SUPERUSERS 覆盖 nonebot 的 SUPERUSERS，保证单一来源。"""
    cfg = get_config()
    if cfg.xingchao_superusers:
        get_driver().config.superusers = {str(uid) for uid in cfg.xingchao_superusers}
        logger.info(f"已从 XINGCHAO_SUPERUSERS 同步超管：{cfg.xingchao_superusers}")
