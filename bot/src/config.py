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
    xingchao_panel_password: str = ""  # Web 管理面板密码；留空则启动时随机生成并打印在日志
    xingchao_ai_base_url: str = ""  # OpenAI 兼容 API 地址（B.AI: https://api.b.ai/v1）
    xingchao_ai_api_key: str = ""  # 对应 API Key；两者任一为空则 AI 功能自动禁用
    xingchao_developer_id: int = 2217021563  # 开发者 QQ（AI 介绍开发者时使用并自动 @）
    xingchao_developer_blog: str = "https://xuyi.dev"  # 开发者博客
    xingchao_developer_site: str = "https://xingchao.dev"  # 项目官网

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
    """惰性单例：首次调用需在 nonebot.init() 之后。

    NoneBot 配置源以 .env 文件为主，OS 环境变量仅能覆盖文件中已存在的同名项；
    为保证 compose 注入的 XINGCHAO_* 变量（如 AI 配置）总能生效，
    这里再用 os.environ 显式覆盖一次（环境变量优先级最高）。
    """
    global _config
    if _config is None:
        import os

        data = get_driver().config.model_dump()
        for field in Config.model_fields:
            env_val = os.getenv(field.upper())
            if env_val is not None:
                data[field] = env_val
        _config = Config.model_validate(data)
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
