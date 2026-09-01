"""Mock NapCat：本地开发用，模拟 NapCat 的反向 WebSocket 客户端。

不登录真实 QQ，即可验证 bot 的 WS 握手、token 鉴权、事件处理与回复动作全链路。

用法：
    cd bot && python ../scripts/mock_napcat.py            # 跑一轮预置场景
    python ../scripts/mock_napcat.py --interactive        # 手动输入群消息文本
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import websockets

WS_URL = "ws://127.0.0.1:8080/onebot/v11/ws"
SELF_ID = 10000
GROUP_ID = 555666777
USER_ID = 777888999
SUPERUSER_ID = 10001
TOKEN = "local-test-token-123456"  # 与 bot/.env 的 ONEBOT_ACCESS_TOKEN 一致


def meta_connect() -> dict:
    return {
        "time": int(time.time()), "self_id": SELF_ID, "post_type": "meta_event",
        "meta_event_type": "lifecycle", "sub_type": "connect",
    }


def parse_segments(text: str) -> list[dict]:
    """把 '@123456' 文本转成 at 段，其余为 text 段（模拟真实消息段）。"""
    segs: list[dict] = []
    for part in re.split(r"(@\d+)", text):
        if not part:
            continue
        if re.fullmatch(r"@\d+", part):
            segs.append({"type": "at", "data": {"qq": part[1:]}})
        else:
            segs.append({"type": "text", "data": {"text": part}})
    return segs


def group_msg(text: str, user_id: int = USER_ID, group_id: int = GROUP_ID) -> dict:
    segments = parse_segments(text)
    plain = "".join(seg["data"]["text"] for seg in segments if seg["type"] == "text")
    return {
        "time": int(time.time()), "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": int(time.time()),
        "user_id": user_id, "group_id": group_id,
        "message": segments,
        "raw_message": plain, "font": 0,
        "sender": {"user_id": user_id, "nickname": "tester", "card": "tester"},
    }


def group_request_event(user_id: int, comment: str, group_id: int = GROUP_ID) -> dict:
    return {
        "time": int(time.time()), "self_id": SELF_ID, "post_type": "request",
        "request_type": "group", "sub_type": "add", "group_id": group_id,
        "user_id": user_id, "comment": comment, "flag": f"flag_{user_id}_{int(time.time())}",
    }


def group_decrease_event(user_id: int, sub_type: str = "leave", group_id: int = GROUP_ID) -> dict:
    return {
        "time": int(time.time()), "self_id": SELF_ID, "post_type": "notice",
        "notice_type": "group_decrease", "sub_type": sub_type,
        "group_id": group_id, "user_id": user_id, "operator_id": 0,
    }


def group_increase_event(user_id: int, group_id: int = GROUP_ID) -> dict:
    return {
        "time": int(time.time()), "self_id": SELF_ID, "post_type": "notice",
        "notice_type": "group_increase", "sub_type": "approve",
        "group_id": group_id, "user_id": user_id, "operator_id": 0,
    }


async def send_event_and_collect(ws, event: dict, timeout: float = 5.0) -> list[dict]:
    """发送一个事件，收集 bot 下发的动作请求（并模拟 NapCat 应答成功）。"""
    responses: list[dict] = []
    await ws.send(json.dumps(event, ensure_ascii=False))
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            break
        data = json.loads(raw)
        if "action" in data:  # bot -> NapCat 的 API 调用
            print(f"  [bot 动作] {data['action']} -> {data['params']}")
            responses.append(data)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": data.get("echo")}))
        else:
            print(f"  [bot ->] {data}")
    return responses


async def run(interactive: bool) -> None:
    async with websockets.connect(
        WS_URL,
        additional_headers={
            "Authorization": f"Bearer {TOKEN}",
            # OneBot v11 适配器要求：反向 WS 连接必须携带 X-Self-ID 头
            "X-Self-ID": str(SELF_ID),
        },
    ) as ws:
        print(f"已连接 {WS_URL}")
        await ws.send(json.dumps(meta_connect()))
        print("已发送 lifecycle connect 元事件")

        if interactive:
            print("输入要发到白名单群的消息文本，Ctrl+C 退出：")
            loop = asyncio.get_running_loop()
            while True:
                text = await loop.run_in_executor(None, input, "群消息> ")
                if not text.strip():
                    continue
                if text.startswith("join "):
                    uid = int(text.split()[1])
                    await send_event_and_collect(ws, group_increase_event(uid))
                    continue
                if text.startswith("request "):
                    comment = text[8:]
                    await send_event_and_collect(
                        ws, group_request_event(13572468, comment)
                    )
                    continue
                if text.startswith("leave "):
                    uid = int(text.split()[1])
                    await send_event_and_collect(ws, group_decrease_event(uid))
                    continue
                if text == "kickme":
                    await send_event_and_collect(
                        ws, group_decrease_event(SELF_ID, sub_type="kick_me")
                    )
                    continue
                user_id = SUPERUSER_ID if text.startswith("!") else USER_ID
                await send_event_and_collect(ws, group_msg(text.lstrip("!"), user_id=user_id))
            return

        scenarios = [
            ("非白名单群 /ping（应无回复）", group_msg("/ping", group_id=999000111)),
            ("白名单群 /ping（应回 pong）", group_msg("/ping")),
            ("白名单群 /id", group_msg("/id")),
            ("白名单群 /help", group_msg("/help")),
            ("关键词「你好星潮」", group_msg("你好星潮")),
            ("冷却期内重复（应无第二次回复）", group_msg("你好星潮")),
            ("成员进群（应触发欢迎）", group_increase_event(24681012)),
            ("成员退群（应群内播报）", group_decrease_event(24681012)),
        ]
        for name, event in scenarios:
            print(f"\n== {name}")
            await send_event_and_collect(ws, event)

        print("\n全部场景发送完毕。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true", help="手动输入消息测试")
    asyncio.run(run(parser.parse_args().interactive))
