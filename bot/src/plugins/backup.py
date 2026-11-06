"""数据库定时备份：每天凌晨对 SQLite 做在线备份并 gzip，保留最近 N 份。

- 备份目录：与数据库同级的 backups/（容器内为 /app/data/backups，随 ./data 挂载持久化）
- 时间：每天本地 03:30；启动后若最近一份备份超过 24 小时，也会补做一次
- 做法：sqlite3 在线 backup API（不锁库、不会拷出写到一半的文件），备份后 gzip 压缩
- 配置（kv）：backup_enabled（默认开）/ backup_keep（保留份数，默认 7）
- 指令（超管）：/备份 立即备份并列出现有备份
- 恢复：停机后解压 .db.gz 覆盖 data/xingchao.db 即可
"""

from __future__ import annotations

import asyncio
import gzip
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from nonebot import get_driver, on_command
from nonebot.log import logger

from src.config import get_config
from src.permission import SUPERUSER
from src.store import get_store

BACKUP_HOUR, BACKUP_MINUTE = 3, 30
DEFAULT_KEEP = 7
_driver = get_driver()
_task: asyncio.Task | None = None


def backup_dir() -> Path:
    return get_config().xingchao_db_path.parent / "backups"


def make_backup(db_path: Path, out_dir: Path, now: datetime | None = None) -> Path:
    """对 db_path 做一次在线备份，返回 .db.gz 路径（同步，供线程调用）。"""
    now = now or datetime.now()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    raw = out_dir / f"xingchao-{stamp}.db"
    gz = out_dir / f"xingchao-{stamp}.db.gz"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with raw.open("rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw.unlink()
    return gz


def prune_backups(out_dir: Path, keep: int) -> list[Path]:
    """只保留最新 keep 份（按文件名时间戳），返回被删除的。"""
    files = sorted(out_dir.glob("xingchao-*.db.gz"))
    doomed = files[: max(0, len(files) - max(1, keep))]
    for f in doomed:
        f.unlink(missing_ok=True)
    return doomed


def latest_backup_age(out_dir: Path, now_ts: float | None = None) -> float | None:
    """最近一份备份距今秒数；没有则 None。"""
    files = sorted(out_dir.glob("xingchao-*.db.gz"))
    if not files:
        return None
    return (now_ts or time.time()) - files[-1].stat().st_mtime


def seconds_until_next(now: datetime) -> float:
    target = now.replace(hour=BACKUP_HOUR, minute=BACKUP_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _keep() -> int:
    raw = await get_store().get_kv("backup_keep")
    return max(1, int(raw)) if raw and raw.isdigit() else DEFAULT_KEEP


async def run_backup() -> tuple[Path, int]:
    """执行一次备份并清理旧份，返回 (新文件, 删除份数)。"""
    path = await asyncio.to_thread(make_backup, get_config().xingchao_db_path, backup_dir())
    removed = await asyncio.to_thread(prune_backups, backup_dir(), await _keep())
    return path, len(removed)


async def _enabled() -> bool:
    return (await get_store().get_kv("backup_enabled")) != "false"


async def _loop() -> None:
    age = await asyncio.to_thread(latest_backup_age, backup_dir())
    if (age is None or age > 86400) and await _enabled():
        await asyncio.sleep(60)  # 等启动完成再备份
        await _safe_run()
    while True:
        await asyncio.sleep(seconds_until_next(datetime.now()))
        if await _enabled():
            await _safe_run()


async def _safe_run() -> None:
    try:
        path, removed = await run_backup()
        logger.info(f"数据库已备份：{path.name}（{path.stat().st_size // 1024} KB），清理旧备份 {removed} 份")
    except Exception:
        logger.exception("数据库备份失败")


@_driver.on_startup
async def _start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


backup_cmd = on_command("备份", aliases={"backup"}, rule=SUPERUSER, priority=1, block=True)


@backup_cmd.handle()
async def handle_backup() -> None:
    try:
        path, removed = await run_backup()
    except Exception as e:
        logger.exception("手动备份失败")
        await backup_cmd.finish(f"备份失败：{e}")
    files = sorted(backup_dir().glob("xingchao-*.db.gz"), reverse=True)
    lines = [f"已备份：{path.name}（{path.stat().st_size // 1024} KB）"]
    if removed:
        lines.append(f"已清理旧备份 {removed} 份")
    lines.append(f"现有 {len(files)} 份，位于 {backup_dir()}：")
    lines += [f"• {f.name}" for f in files[:8]]
    await backup_cmd.finish("\n".join(lines))
