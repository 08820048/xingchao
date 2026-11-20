"""表情包库：群友存下的表情包 + 机器人账号 QQ 收藏的表情包，让它能在聊天里合时宜地发表情包。

两个来源合在一起用（编号：本地库 "L<id>"，QQ 收藏为 url 的短哈希）：
- 本地库（主）：群友回复表情包说「存表情」、或机器人自主收集群里多人发过的表情（见 stickersave 插件）。
  文件存 data/stickers/，存之前视觉模型一次性审核 + 读懂（图上文字 / 意思 / 适用场合），本地再查二维码
- QQ 收藏（辅）：NapCat fetch_custom_face，只能读机器人账号手动收藏的；视觉模型打 2–6 字标签存 kv
挑选：按当前聊天内容检索最相关的一批，本群存的优先，同一张在同一个群里短时间内不重复发；
主动发言的决策里有 sticker 动作（按编号挑），AI 工具 send_sticker(mood) 按心情发一张。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

from src import llm
from src.store import get_store

REFRESH_SECONDS = 6 * 3600
TAGS_PER_REFRESH = 6
TAGS_PER_DAY = 40
CATALOG_SIZE = 20
TAG_PROMPT = (
    "用 2 到 6 个字概括这张表情包表达的情绪或梗（如：无语、开心、狗头、委屈、吃瓜、加油）；"
    "如果图上有文字，用文字含义。只输出标签，不要标点和解释。"
)

_urls: dict[str, str] = {}      # sticker_id → 当前 url
_last_refresh = 0.0
_tag_day: tuple[str, int] = ("", 0)


def sticker_id(url: str) -> str:
    """用不含查询参数的 url 做稳定 ID（CDN 的签名参数会变）。"""
    return hashlib.md5(url.split("?")[0].encode()).hexdigest()[:12]


def parse_face_list(raw: Any) -> list[str]:
    """fetch_custom_face 的返回在不同版本里可能是 url 列表或 {url:...} 列表。"""
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("list") or raw.get("urls") or []
    urls: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        u = item if isinstance(item, str) else (item or {}).get("url") if isinstance(item, dict) else None
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
    return urls


async def _tags() -> dict[str, str]:
    raw = await get_store().get_kv("sticker_tags")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _save_tags(tags: dict[str, str]) -> None:
    if len(tags) > 300:  # 只留最近的 300 条
        tags = dict(list(tags.items())[-300:])
    await get_store().set_kv("sticker_tags", json.dumps(tags, ensure_ascii=False))


def _clean_tag(raw: str | None) -> str | None:
    tag = re.sub(r"[\s，。,.！!？?「」\"'`]", "", raw or "")[:8]
    return tag or None


async def _tag_one(url: str) -> str | None:
    from src.plugins.visionguard import _download_as_data_uri

    ref = await _download_as_data_uri(url) or url
    return _clean_tag(await llm.generate_vision(ref, TAG_PROMPT, max_tokens=300))


async def ensure_library(bot, *, force: bool = False) -> int:
    """刷新 QQ 收藏列表并给新表情打标签；返回当前可用的表情包总数（收藏里有标签的 + 本地库）。"""
    global _last_refresh, _tag_day
    now = time.time()
    # 收藏为空或接口不支持时也按间隔重试，不要每次决策都调一遍接口
    if not force and now - _last_refresh < REFRESH_SECONDS:
        return len([i for i in _urls if i in await _tags()]) + len(await _local_stickers())
    _last_refresh = now
    try:
        urls = parse_face_list(await bot.call_api("fetch_custom_face", count=48))
    except Exception:
        logger.debug("获取收藏表情失败", exc_info=True)  # 保留上次拉到的列表
        return len([i for i in _urls if i in await _tags()]) + len(await _local_stickers())
    _urls.clear()
    _urls.update({sticker_id(u): u for u in urls})
    tags = await _tags()
    day = datetime.now().strftime("%Y-%m-%d")
    used = _tag_day[1] if _tag_day[0] == day else 0
    todo = [i for i in _urls if i not in tags][: min(TAGS_PER_REFRESH, max(0, TAGS_PER_DAY - used))]
    changed = False
    for sid in todo:
        try:
            tag = await _tag_one(_urls[sid])
        except Exception:
            logger.debug("表情包打标签失败", exc_info=True)
            continue
        used += 1
        if tag:
            tags[sid] = tag
            changed = True
    _tag_day = (day, used)
    if changed:
        await _save_tags(tags)
    return len([i for i in _urls if i in tags]) + len(await _local_stickers())


# ---------------------------------------------------------------- 本地库（群友存的）

MAX_LIBRARY = 500                 # 本地库上限：满了不再自主收集，指令存会提示超管清理
MAX_BYTES = 3 * 1024 * 1024       # 单张上限（与图片审核一致）
GROUP_COOLDOWN = 30 * 60          # 同一张在同一个群里 30 分钟内不重复发
RELEVANT_N = 12                   # 菜单里按聊天内容挑出的相关表情数，其余随机补满 CATALOG_SIZE
SAME_GROUP_BONUS = 2.0            # 本群存的优先

SAVE_PROMPT = (
    "这是群友想收藏的一张表情包 / 梗图。只输出一个 JSON 对象（不要代码块、不要多余文字）：\n"
    '{"safe": true, "text": "图上的文字，没有就留空", "meaning": "这张图表达什么，不超过15字", '
    '"usage": "适合在什么聊天场合发，不超过20字", "tags": "2到4个关键词，用逗号分隔"}\n'
    "safe 填 false 的情况：色情或裸露、血腥暴力、翻墙/VPN、广告引流（价目表、联系方式、推广海报）、"
    "涉政内容、针对真实个人的侮辱或隐私。普通梗图、动漫、熊猫头、猫猫狗狗、聊天截图梗都算 safe。只输出 JSON。"
)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAGIC = (
    (b"GIF87a", "image/gif", "gif"), (b"GIF89a", "image/gif", "gif"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"), (b"\xff\xd8\xff", "image/jpeg", "jpg"),
)

_local: list[dict] | None = None              # 本地库缓存；增删时置 None
_recent: dict[int, dict[str, float]] = {}     # group_id → {sticker_id: 上次发送时间}


def sticker_dir():
    from src.config import get_config

    return get_config().xingchao_db_path.parent / "stickers"


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """按文件头识别 (mime, 扩展名)；不是常见图片返回 None。"""
    for magic, mime, ext in _MAGIC:
        if data.startswith(magic):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


async def _local_stickers() -> list[dict]:
    global _local
    if _local is None:
        _local = await get_store().list_stickers()
    return _local


def _invalidate() -> None:
    global _local
    _local = None


def _label(row: dict) -> str:
    """菜单里的一行描述：「图上文字」意思·场合（控制长度，菜单要放二十张）。"""
    text = (row.get("text") or "").strip().replace("\n", " ")
    head = f"「{text[:10]}」" if text else ""
    body = row.get("meaning") or row.get("tags") or ""
    usage = row.get("usage") or ""
    out = f"{head}{body}" + (f"·{usage}" if usage else "")
    return out[:30]


async def _candidates() -> list[dict]:
    """两个来源合并：[{id, label, terms_text, group_id}]。"""
    out: list[dict] = []
    for row in await _local_stickers():
        out.append({
            "id": f"L{row['id']}", "label": _label(row), "group_id": int(row["group_id"]),
            "terms_text": " ".join([row.get("text") or "", row.get("meaning") or "",
                                    row.get("usage") or "", row.get("tags") or ""]),
        })
    tags = await _tags()
    for sid in _urls:
        if sid in tags:
            out.append({"id": sid, "label": tags[sid], "group_id": 0, "terms_text": tags[sid]})
    return out


def _score(context_terms: set[str], cand: dict, group_id: int) -> float:
    from src.textindex import term_weight, terms

    shared = context_terms & terms(cand["terms_text"])
    score = sum(term_weight(t) for t in shared)
    if group_id and cand["group_id"] == group_id:
        score += SAME_GROUP_BONUS
    return score


def _cooling(group_id: int, now: float | None = None) -> set[str]:
    now = time.time() if now is None else now
    recent = _recent.get(group_id, {})
    for sid in [k for k, ts in recent.items() if now - ts >= GROUP_COOLDOWN]:
        recent.pop(sid, None)
    return set(recent)


async def catalog(group_id: int = 0, context: str = "") -> tuple[str, list[str]]:
    """给决策提示词用：按聊天内容挑最相关的 RELEVANT_N 张（本群存的加分），再随机补满 CATALOG_SIZE；
    刚在本群发过的不放进来。返回 (“1.xxx 2.xxx…”, 编号→sticker_id)。"""
    from src.textindex import terms

    cands = [c for c in await _candidates() if c["id"] not in _cooling(group_id)]
    if not cands:
        return "", []
    ctx = terms(context)
    scored = sorted(cands, key=lambda c: (_score(ctx, c, group_id), random.random()), reverse=True)
    top = [c for c in scored[:RELEVANT_N] if _score(ctx, c, group_id) > 0]
    rest = [c for c in cands if c not in top]
    random.shuffle(rest)
    pick = (top + rest)[:CATALOG_SIZE]
    return " ".join(f"{n}.{c['label']}" for n, c in enumerate(pick, 1)), [c["id"] for c in pick]


async def pick_by_mood(mood: str, group_id: int = 0) -> str | None:
    """按心情/梗挑一张：最相关的前三张里随机；都不相关就随机（本群存的优先）；库为空返回 None。"""
    from src.textindex import terms

    cands = [c for c in await _candidates() if c["id"] not in _cooling(group_id)]
    if not cands:
        return None
    mood = (mood or "").strip()
    ctx = terms(mood)
    scored = sorted(
        ((_score(ctx, c, group_id) + (3.0 if mood and mood in c["terms_text"] else 0.0), c) for c in cands),
        key=lambda x: (x[0], random.random()), reverse=True,
    )
    best = [c for sc, c in scored[:3] if sc > SAME_GROUP_BONUS] or [c for sc, c in scored[:3] if sc > 0]
    return random.choice(best or [c for _, c in scored])["id"]


async def sticker_message(sid: str) -> Message | None:
    """按编号生成可发送的消息（本地库读文件转 base64；QQ 收藏用 url）。"""
    if sid.startswith("L") and sid[1:].isdigit():
        row = next((r for r in await _local_stickers() if r["id"] == int(sid[1:])), None)
        if row is None:
            return None
        path = sticker_dir() / row["file"]
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            logger.warning(f"表情包文件丢失：{path}")
            return None
        file = "base64://" + base64.b64encode(data).decode()
    else:
        url = _urls.get(sid)
        if not url:
            return None
        file = url
    return Message([MessageSegment("image", {"file": file, "sub_type": 1, "summary": "[动画表情]"})])


async def note_used(group_id: int, sid: str) -> None:
    """发出去之后调用：本群冷却 + 本地库计数。"""
    _recent.setdefault(group_id, {})[sid] = time.time()
    if sid.startswith("L") and sid[1:].isdigit():
        try:
            await get_store().bump_sticker_use(int(sid[1:]))
        except Exception:
            logger.debug("记录表情包使用失败", exc_info=True)


def parse_save_result(raw: str | None) -> dict | None:
    """解析视觉模型的审核+描述结果；格式不对返回 None（宁可不存）。"""
    m = _JSON_RE.search(raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    def clean(key: str, n: int) -> str:
        return re.sub(r"\s+", " ", str(data.get(key) or "")).strip()[:n]

    safe = data.get("safe")
    return {
        "safe": safe is True or str(safe).lower() == "true",
        "text": clean("text", 40), "meaning": clean("meaning", 20),
        "usage": clean("usage", 30), "tags": clean("tags", 30).replace("，", ","),
    }


async def save_image(data: bytes, *, group_id: int, saved_by: int, source: str) -> tuple[str, dict | None]:
    """把一张图存进本地库。返回 (结果码, 记录)：
    ok / duplicate（已存过，记录为已有那张）/ too_big / not_image / full / qrcode / no_ai / unsafe / failed。"""
    if len(data) > MAX_BYTES:
        return "too_big", None
    kind = sniff_image(data)
    if kind is None:
        return "not_image", None
    sha = hashlib.sha256(data).hexdigest()
    store = get_store()
    existing = await store.get_sticker_by_sha(sha)
    if existing:
        return "duplicate", existing
    if len(await _local_stickers()) >= MAX_LIBRARY:
        return "full", None
    from src.plugins.visionguard import _decode_qr

    if await asyncio.to_thread(_decode_qr, data):
        return "qrcode", None
    if not await llm.is_ai_enabled():
        return "no_ai", None
    mime, ext = kind
    ref = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    info = parse_save_result(await llm.generate_vision(ref, SAVE_PROMPT, max_tokens=600))
    if info is None:
        return "failed", None
    if not info["safe"]:
        return "unsafe", None
    name = f"{sha[:16]}.{ext}"
    folder = sticker_dir()
    try:
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        tmp = folder / (name + ".tmp")
        await asyncio.to_thread(tmp.write_bytes, data)
        await asyncio.to_thread(tmp.replace, folder / name)
    except OSError:
        logger.exception("写入表情包文件失败")
        return "failed", None
    new_id = await store.add_sticker(
        sha=sha, file=name, mime=mime, text=info["text"], meaning=info["meaning"],
        usage=info["usage"], tags=info["tags"], group_id=group_id, saved_by=saved_by, source=source,
    )
    _invalidate()
    if new_id is None:  # 并发下别人刚存了同一张
        return "duplicate", await store.get_sticker_by_sha(sha)
    return "ok", await store.get_sticker(new_id)


async def delete(sticker_id: int) -> dict | None:
    row = await get_store().delete_sticker(sticker_id)
    if row is None:
        return None
    _invalidate()
    try:
        await asyncio.to_thread((sticker_dir() / row["file"]).unlink, missing_ok=True)
    except OSError:
        logger.warning(f"删除表情包文件失败：{row['file']}")
    return row


def describe(row: dict) -> str:
    """给人看的一行：#12「我裂开了」崩溃翻车·自己翻车时"""
    return f"#{row['id']}{_label(row)}"
