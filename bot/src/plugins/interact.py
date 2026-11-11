"""群内互动小能力：点赞、戳一戳、群文件查询、消息已读、机器人签名。

- /赞我：给你的 QQ 资料卡点赞（白名单群所有人；每人每天最多一次，QQ 自身也有每日上限）
- poke_member（AI 工具）：戳一戳某个群成员（同群 20 秒冷却，防刷）。机器人被戳时的回应由第三方
  pokepoke_miss 负责，这里不重复实现
- /群文件 [关键词]：列出群文件根目录（可按关键词过滤），带下载链接；AI 工具 list_group_files
  注意：不提供「上传群文件」——NapCat 在独立容器里，读不到 bot 容器的本地文件
- 已读标记：后台每 10 分钟对白名单群调用 mark_group_msg_as_read，避免机器人账号未读堆积
  （kv mark_read_enabled=false 关闭）
- /签名 <内容>（超管）：修改机器人账号的个性签名；AI 工具 set_bot_signature
- 输入中状态：机器人在私聊里回复前显示"正在输入"（NapCat set_input_status，仅私聊有效，失败静默忽略）

NapCat 接口：send_like / group_poke / get_group_root_files / get_group_files_by_folder /
get_group_file_url / mark_group_msg_as_read / set_self_longnick / set_input_status。
各接口的参数与返回格式随 NapCat 版本可能有差异，失败只返回友好提示、不抛异常。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from nonebot import get_bots, get_driver, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from src.permission import GROUP_WHITELIST, SUPERUSER, disabled_groups, merged_whitelist
from src.store import get_store

LIKE_TIMES = 10
POKE_COOLDOWN = 20.0
MARK_READ_INTERVAL = 600
FILE_LIST_MAX = 15
SIGNATURE_MAX = 60

_liked: dict[int, str] = {}          # user_id -> 点过赞的日期
_last_poke: dict[int, float] = {}    # group_id -> monotonic 时间戳
_worker_started = False


def _err(e: Exception) -> str:
    return str(getattr(e, "info", None) or e)[:80]


# ---------------------------------------------------------------- 点赞

async def give_like(bot: Bot, user_id: int) -> tuple[bool, str]:
    today = datetime.now().strftime("%Y-%m-%d")
    if _liked.get(user_id) == today:
        return False, "今天已经给你点过赞啦，明天再来～"
    try:
        await bot.call_api("send_like", user_id=user_id, times=LIKE_TIMES)
    except Exception as e:
        return False, f"点赞失败：{_err(e)}（可能今天的赞已用完，或对方设置了限制）"
    _liked[user_id] = today
    return True, f"已给你点了 {LIKE_TIMES} 个赞，去资料卡看看～"


like_cmd = on_command("赞我", aliases={"点赞"}, rule=GROUP_WHITELIST, priority=5, block=True)


@like_cmd.handle()
async def handle_like(matcher: Matcher, bot: Bot, event: GroupMessageEvent) -> None:
    _, msg = await give_like(bot, event.user_id)
    await matcher.send(msg)


# ---------------------------------------------------------------- 戳一戳

async def poke(bot: Bot, group_id: int, user_id: int) -> tuple[bool, str]:
    now = time.monotonic()
    last = _last_poke.get(group_id)
    if last is not None and now - last < POKE_COOLDOWN:
        return False, "刚戳过，稍等一会儿再戳（防刷屏）"
    try:
        await bot.call_api("group_poke", group_id=group_id, user_id=user_id)
    except Exception as e:
        return False, f"戳一戳失败：{_err(e)}"
    _last_poke[group_id] = now
    return True, f"已戳了 {user_id} 一下"


# ---------------------------------------------------------------- 群文件

def _fmt_size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return "?"


async def list_files(bot: Bot, group_id: int, keyword: str = "") -> list[dict]:
    """群文件根目录里（含一层子文件夹）匹配关键词的文件；关键词为空返回根目录文件。"""
    root = await bot.call_api("get_group_root_files", group_id=group_id)
    root = root or {}
    files = list(root.get("files") or [])
    kw = keyword.strip().lower()
    if kw:
        for folder in (root.get("folders") or [])[:5]:
            fid = folder.get("folder_id")
            if not fid:
                continue
            try:
                sub = await bot.call_api("get_group_files_by_folder", group_id=group_id, folder_id=fid)
            except Exception:
                continue
            files += list((sub or {}).get("files") or [])
        files = [f for f in files if kw in str(f.get("file_name", "")).lower()]
    files.sort(key=lambda f: f.get("modify_time") or f.get("upload_time") or 0, reverse=True)
    return files


async def file_url(bot: Bot, group_id: int, f: dict) -> str | None:
    try:
        res = await bot.call_api(
            "get_group_file_url", group_id=group_id, file_id=f.get("file_id"), busid=f.get("busid", 102)
        )
    except Exception:
        logger.warning("获取群文件链接失败", exc_info=True)
        return None
    return str((res or {}).get("url") or "") or None


async def files_text(bot: Bot, group_id: int, keyword: str = "", with_links: bool = True) -> str:
    try:
        files = await list_files(bot, group_id, keyword)
    except Exception as e:
        return f"读取群文件失败：{_err(e)}（NapCat 不支持或机器人无权限）"
    if not files:
        return f"没有找到与「{keyword}」相关的群文件。" if keyword else "群文件根目录是空的。"
    lines = [f"群文件（共 {len(files)} 个，显示最近 {min(len(files), FILE_LIST_MAX)} 个）："]
    for f in files[:FILE_LIST_MAX]:
        line = f"• {f.get('file_name', '?')}（{_fmt_size(f.get('size'))}，{f.get('uploader_name', '?')}）"
        if with_links and len(files) <= 5:
            url = await file_url(bot, group_id, f)
            if url:
                line += f"\n  {url}"
        lines.append(line)
    return "\n".join(lines)


files_cmd = on_command("群文件", rule=GROUP_WHITELIST, priority=5, block=True)


@files_cmd.handle()
async def handle_files(matcher: Matcher, bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    await matcher.send(await files_text(bot, event.group_id, args.extract_plain_text().strip()))


# ---------------------------------------------------------------- 签名

async def set_signature(bot: Bot, text: str) -> tuple[bool, str]:
    text = " ".join(text.split())[:SIGNATURE_MAX]
    try:
        await bot.call_api("set_self_longnick", longNick=text)
    except Exception as e:
        return False, f"修改签名失败：{_err(e)}"
    return True, f"已把我的个性签名改为「{text}」" if text else "已清空我的个性签名"


sign_cmd = on_command("签名", rule=SUPERUSER, priority=1, block=True)


@sign_cmd.handle()
async def handle_signature(matcher: Matcher, bot: Bot, args: Message = CommandArg()) -> None:
    ok, msg = await set_signature(bot, args.extract_plain_text().strip())
    await matcher.send(("✅ " if ok else "⚠️ ") + msg)


# ---------------------------------------------------------------- 输入中状态（仅私聊）

async def typing_private(bot: Bot, user_id: int) -> None:
    """私聊回复前显示"正在输入"；NapCat 不支持或失败时静默忽略。"""
    try:
        await bot.call_api("set_input_status", user_id=str(user_id), event_type=1)
    except Exception:
        logger.debug("set_input_status 不可用", exc_info=True)


# ---------------------------------------------------------------- 已读标记

async def mark_read_once(bot: Bot) -> int:
    n = 0
    for gid in sorted(merged_whitelist() - disabled_groups()):
        try:
            await bot.call_api("mark_group_msg_as_read", group_id=gid)
            n += 1
        except Exception:
            logger.debug("mark_group_msg_as_read 失败", exc_info=True)
            return n  # 接口不可用就别每个群都试一遍
    return n


async def _mark_loop() -> None:
    while True:
        await asyncio.sleep(MARK_READ_INTERVAL)
        try:
            if await get_store().get_kv("mark_read_enabled") == "false":
                continue
            for bot in list(get_bots().values()):
                if isinstance(bot, Bot):
                    await mark_read_once(bot)
        except Exception:
            logger.exception("已读标记循环异常")


@get_driver().on_startup
async def _start_mark_loop() -> None:
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.get_event_loop().create_task(_mark_loop())
