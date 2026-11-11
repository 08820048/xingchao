"""版本更新通知：每次部署带来新版本时，在白名单群推送一条规范的更新公告。

- 数据：src/changelog.json（最新版本在最前），字段 version/date/added/improved/fixed，
  notify=false 的版本只记录不推送（小修小补、纯内部改动）
- 触发：Bot 连接 NapCat 后，若 changelog 最新版本 ≠ kv update_announced_version 则推送并记录；
  推送与「已上线」合并：有待推送更新时，presence 不再单独发「已上线」
- 开关：kv update_notify=false 关闭推送（仍会记录版本，避免之后一次性补发）
- 指令：/更新日志 （白名单群或超管私聊）查看最新一次更新
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from src.permission import BASIC, disabled_groups, merged_whitelist
from src.store import get_store

CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "changelog.json"
SEND_GAP_SECONDS = 0.8
ANNOUNCE_DELAY_SECONDS = 8  # 等 NapCat 就绪，且晚于 presence 的 5 秒检查
_SECTIONS = (("added", "✨ 新增"), ("improved", "🔧 优化"), ("fixed", "🐛 修复"))
_driver = get_driver()
_announced_in_process = False


def load_changelog() -> list[dict[str, Any]]:
    try:
        data = json.loads(CHANGELOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("读取 changelog.json 失败")
        return []
    return [e for e in data if isinstance(e, dict) and e.get("version")] if isinstance(data, list) else []


def format_entry(entry: dict[str, Any]) -> str:
    lines = [f"🚀 星潮 v{entry['version']} 更新", f"📅 {entry.get('date', '')}".rstrip(), "━━━━━━━━━━━━"]
    for key, title in _SECTIONS:
        items = [str(i).strip() for i in entry.get(key, []) if str(i).strip()]
        if items:
            lines.append(title)
            lines.extend(f"  • {i}" for i in items)
            lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("━━━━━━━━━━━━")
    lines.append("发送 /帮助 查看全部功能；对处罚有异议可私聊机器人申诉")
    return "\n".join(line for line in lines if line is not None)


async def pending_entry() -> dict[str, Any] | None:
    """尚未推送过且允许推送的最新版本；无则 None。"""
    store = get_store()
    if (await store.get_kv("update_notify")) == "false":
        return None
    entries = load_changelog()
    if not entries:
        return None
    latest = entries[0]
    if not latest.get("notify", True):
        return None
    if (await store.get_kv("update_announced_version")) == str(latest["version"]):
        return None
    return latest


async def _announce(bot: Bot) -> None:
    global _announced_in_process
    await asyncio.sleep(ANNOUNCE_DELAY_SECONDS)
    entries = load_changelog()
    if not entries:
        return
    store = get_store()
    entry = await pending_entry()
    if entry is None:
        # 不推送的版本（关闭 / notify=false）也记下，避免之后打开开关时补发旧公告
        await store.set_kv("update_announced_version", str(entries[0]["version"]))
        return
    if _announced_in_process:
        return
    _announced_in_process = True
    text = format_entry(entry)
    ok = 0
    for gid in sorted(merged_whitelist() - disabled_groups()):
        try:
            await bot.call_api("send_group_msg", group_id=gid, message=text)
            ok += 1
        except Exception as e:
            logger.warning(f"更新公告发送失败：group={gid} {type(e).__name__}: {e}")
        await asyncio.sleep(SEND_GAP_SECONDS)
    if ok:  # 至少一个群成功才记录，全失败则下次连接重试
        await store.set_kv("update_announced_version", str(entry["version"]))
    else:
        _announced_in_process = False
    logger.info(f"更新公告 v{entry['version']} 已发送：{ok} 个群")


@_driver.on_bot_connect
async def _on_connect(bot: Bot) -> None:
    asyncio.create_task(_announce(bot))


changelog_cmd = on_command("更新日志", aliases={"changelog", "更新"}, rule=BASIC, priority=5, block=True)


@changelog_cmd.handle()
async def handle_changelog() -> None:
    entries = load_changelog()
    if not entries:
        await changelog_cmd.finish("暂无更新记录。")
    await changelog_cmd.finish(format_entry(entries[0]))
