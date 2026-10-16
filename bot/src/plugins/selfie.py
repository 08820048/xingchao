"""星潮自拍：有人想看机器人本人的照片时，从 data/selfies/ 随机发一张。

- 照片目录 data/selfies/（compose 已挂载 ./data，直接往里放 png/jpg/webp/gif 即可，无需重启）
- 触发：`/自拍`（别名 /照片 /selfie）或 AI 工具 send_selfie（“看看你的照片/发张自拍”）
- 发送前压缩：最长边 ≤ 1280、转 JPEG（保留透明图为白底），避免大图发送失败/刷屏
- 每个会话冷却 COOLDOWN 秒；目录为空时如实告知
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
import time
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from PIL import Image

from src.config import get_config
from src.permission import BASIC

EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_SIDE = 1280
COOLDOWN = 20

_last: dict[str, float] = {}


def _dir() -> Path:
    return get_config().xingchao_db_path.parent / "selfies"


def _list_photos() -> list[Path]:
    d = _dir()
    if not d.is_dir():
        return []
    return [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in EXTS]


def _load_jpeg(path: Path) -> bytes:
    with Image.open(path) as im:
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()


async def send_selfie(bot: Bot, event: MessageEvent) -> str:
    """发一张随机自拍到当前会话；返回给调用方（AI/指令）的中文结果。"""
    photos = await asyncio.to_thread(_list_photos)
    if not photos:
        return "我还没有可发的照片（管理员需要把图片放进 data/selfies/ 目录）。"
    key = str(getattr(event, "group_id", None) or f"u{event.user_id}")
    wait = COOLDOWN - (time.monotonic() - _last.get(key, 0))
    if wait > 0:
        return f"刚发过啦，{int(wait) + 1} 秒后再来看吧。"
    path = random.choice(photos)
    try:
        data = await asyncio.to_thread(_load_jpeg, path)
        await bot.send(event, MessageSegment.image("base64://" + base64.b64encode(data).decode()))
    except Exception as e:
        logger.exception(f"自拍发送失败：{path.name}")
        return f"照片发送失败：{e}"
    _last[key] = time.monotonic()
    return "已发送一张我的照片。"


selfie_cmd = on_command("自拍", aliases={"照片", "selfie"}, rule=BASIC, priority=5, block=True)


@selfie_cmd.handle()
async def handle_selfie(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    result = await send_selfie(bot, event)
    if not result.startswith("已发送"):
        await matcher.finish(result)
