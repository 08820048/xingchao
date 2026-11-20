#!/usr/bin/env python3
"""NapCat/QQ 掉线看门狗：检测 KickedOffLine，邮件告警并尝试重启 NapCat。

为什么放在宿主机：QQ 掉线时 bot 可能收不到事件，容器内脚本也无法可靠地重启自己。
本脚本监听 `docker logs xingchao-napcat`，发现掉线后：
  1. 发邮件到 `.env` 的 MAIL_NOTICE.username（复用 BotMailNotice 的 SMTP 配置）
  2. 尝试 `docker restart xingchao-napcat`（默认开启，带冷却和每小时次数上限）
  3. 重启后继续看日志：快速登录成功 / 需要扫码，把结果再发一封邮件

用法：
  python3 scripts/napcat-watchdog.py                 # 前台循环（推荐 systemd）
  python3 scripts/napcat-watchdog.py --once          # 只检查一次（可配 cron）
  python3 scripts/napcat-watchdog.py --no-restart    # 只告警，不自动重启
  python3 scripts/napcat-watchdog.py --no-email      # 只重启，不发邮件
  python3 scripts/napcat-watchdog.py --dry-run       # 只打印，不告警不重启

环境变量（可选）：
  NAPCAT_CONTAINER       容器名，默认 xingchao-napcat
  WATCHDOG_INTERVAL      检查间隔秒数，默认 20
  WATCHDOG_COOLDOWN      两次重启最小间隔秒数，默认 300
  WATCHDOG_MAX_RESTARTS  每 3600 秒最多重启次数，默认 3
  WATCHDOG_STATE         状态文件，默认 data/napcat-watchdog.json
  WATCHDOG_LOG           日志文件，默认 data/logs/napcat-watchdog.log

安装为 systemd 服务见 scripts/napcat-watchdog.service。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import signal
import smtplib
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = os.getenv("NAPCAT_CONTAINER", "xingchao-napcat")
INTERVAL = max(5, int(os.getenv("WATCHDOG_INTERVAL", "20")))
COOLDOWN = max(60, int(os.getenv("WATCHDOG_COOLDOWN", "300")))
MAX_RESTARTS = max(1, int(os.getenv("WATCHDOG_MAX_RESTARTS", "3")))
STATE_PATH = Path(os.getenv("WATCHDOG_STATE", str(ROOT / "data/napcat-watchdog.json")))
LOG_PATH = Path(os.getenv("WATCHDOG_LOG", str(ROOT / "data/logs/napcat-watchdog.log")))
DOCKER = shutil.which("docker") or "/usr/bin/docker"

_stop = False


def log(msg: str) -> None:
    line = f"{datetime.now().astimezone().strftime('%F %T')} {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.is_file():
        return env
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def _mail_config() -> dict | None:
    raw = os.getenv("MAIL_NOTICE") or _load_dotenv().get("MAIL_NOTICE")
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        try:
            cfg = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            log("MAIL_NOTICE 解析失败，跳过邮件")
            return None
    if not isinstance(cfg, dict) or not cfg.get("username") or not cfg.get("password"):
        return None
    if not cfg.get("hostname"):
        return None
    return cfg


def send_mail(subject: str, body: str) -> bool:
    cfg = _mail_config()
    if cfg is None:
        log("未配置 MAIL_NOTICE，跳过邮件")
        return False
    try:
        msg = EmailMessage()
        msg["From"] = cfg["username"]
        msg["To"] = cfg.get("to") or cfg["username"]
        msg["Subject"] = subject
        msg.set_content(body)
        port = int(cfg.get("port") or 587)
        host = cfg["hostname"]
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
            smtp.starttls(context=ssl.create_default_context())
        try:
            smtp.login(cfg["username"], cfg["password"])
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        log(f"邮件已发送：{subject}")
        return True
    except Exception as e:
        log(f"邮件发送失败：{type(e).__name__}: {e}")
        return False


def docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DOCKER, *args], capture_output=True, text=True, timeout=timeout
    )


def container_running() -> bool:
    try:
        out = docker("inspect", "-f", "{{.State.Running}}", CONTAINER, timeout=10)
    except Exception as e:
        log(f"docker inspect 失败：{type(e).__name__}: {e}")
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def napcat_online() -> bool | None:
    """通过 NapCat WebUI 的 get_status 判断 QQ 当前是否在线；失败返回 None。"""
    try:
        webui = json.loads((ROOT / "napcat/config/webui.json").read_text(encoding="utf-8"))
        token = str(webui.get("token") or "")
        if not token:
            return None
        base = "http://127.0.0.1:6099"
        digest = hashlib.sha256((token + ".napcat").encode()).hexdigest()
        req = urllib.request.Request(
            base + "/api/auth/login",
            data=json.dumps({"hash": digest}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            cred = json.loads(r.read().decode())["data"]["Credential"]
        req = urllib.request.Request(
            base + "/api/Debug/call",
            data=json.dumps({"action": "get_status", "params": {}}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {cred}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read().decode())
        return bool(payload["data"]["data"]["online"])
    except Exception as e:
        log(f"查询 QQ 在线状态失败（忽略）：{type(e).__name__}: {e}")
        return None


def tail_logs(n: int = 40) -> str:
    try:
        out = docker("logs", "--tail", str(n), CONTAINER, timeout=15)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        return f"(读取日志失败：{type(e).__name__}: {e})"


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log(f"保存状态失败：{e}")


def _restart_allowed(state: dict, now: float) -> tuple[bool, str]:
    times = [float(t) for t in state.get("restarts", []) if now - float(t) < 3600]
    state["restarts"] = times
    if now - float(state.get("last_restart", 0) or 0) < COOLDOWN:
        return False, f"距上次重启不足 {COOLDOWN} 秒"
    if len(times) >= MAX_RESTARTS:
        return False, f"最近 1 小时已重启 {len(times)} 次（上限 {MAX_RESTARTS}）"
    return True, ""


def _wait_login_result(since: float, timeout: int = 90) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline and not _stop:
        try:
            since_iso = datetime.fromtimestamp(max(0, since - 2)).astimezone().isoformat(
                timespec="seconds"
            )
            out = docker("logs", "--since", since_iso, CONTAINER, timeout=15)
            text = (out.stdout or "") + (out.stderr or "")
        except Exception:
            text = ""
        if "自动快速登录成功" in text:
            return "quick_login_ok"
        if "请扫描下面的二维码" in text or "二维码已保存" in text:
            return "need_qr"
        time.sleep(3)
    return "unknown"


def handle_offline(reason: str, line: str, state: dict, *, no_email: bool, no_restart: bool, dry_run: bool) -> None:
    now = time.time()
    stamp = datetime.now().astimezone().strftime("%F %T")
    log(f"检测到掉线：{reason}")
    if dry_run:
        log("dry-run：不告警、不重启")
        return

    last_alert = float(state.get("last_alert", 0) or 0)
    alerted = now - last_alert >= 120
    if not alerted:
        log("120 秒内已告警过，跳过重复邮件")
    elif not no_email:
        body = (
            f"时间：{stamp}\n"
            f"原因：{reason}\n"
            f"命中日志：{line.strip()[:500]}\n\n"
            f"最近日志：\n{tail_logs(40)[-4000:]}\n"
        )
        send_mail("[星潮] NapCat/QQ 掉线告警", body)
        state["last_alert"] = now

    allowed, why = _restart_allowed(state, now)
    if no_restart:
        log("已禁用自动重启（--no-restart）")
    elif not allowed:
        log(f"跳过自动重启：{why}")
        if alerted and not no_email:
            send_mail("[星潮] NapCat 掉线（未自动重启）", f"{stamp}\n{reason}\n{why}\n{line[:500]}")
    else:
        log(f"正在重启容器 {CONTAINER}…")
        started = time.time()
        try:
            out = docker("restart", CONTAINER, timeout=90)
            if out.returncode != 0:
                raise RuntimeError((out.stderr or out.stdout or "").strip() or "docker restart 失败")
        except Exception as e:
            log(f"重启失败：{type(e).__name__}: {e}")
            if not no_email:
                send_mail("[星潮] NapCat 掉线，自动重启失败", f"{stamp}\n{reason}\n{e}\n")
            return
        state["last_restart"] = started
        state.setdefault("restarts", []).append(started)
        result = _wait_login_result(started)
        text = {
            "quick_login_ok": "重启后自动快速登录成功，机器人应已恢复。",
            "need_qr": "重启后登录态仍失效，需要人工扫码。",
            "unknown": "重启完成，但 90 秒内未确认登录结果，请查看 NapCat 日志。",
        }[result]
        log(text)
        if not no_email:
            send_mail(
                "[星潮] NapCat 已重启",
                f"时间：{stamp}\n原因：{reason}\n{text}\n\n最近日志：\n{tail_logs(30)[-3000:]}\n",
            )


def check_once(state: dict, *, no_email: bool, no_restart: bool, dry_run: bool) -> None:
    now = time.time()
    running = container_running()
    if not running:
        handle_offline("容器未运行", "", state, no_email=no_email, no_restart=no_restart, dry_run=dry_run)
        return

    # 启动后先查一次当前在线状态：避免看门狗启动前已经掉线、日志里又没有 KickedOffLine 的情况。
    if not state.get("startup_checked"):
        state["startup_checked"] = True
        online = napcat_online()
        if online is False:
            state["last_kick"] = now
            handle_offline(
                "QQ 当前处于离线状态", "WebUI get_status: online=false",
                state, no_email=no_email, no_restart=no_restart, dry_run=dry_run,
            )
            return

    since = max(0.0, float(state.get("last_check", 0) or 0) - 2)
    if since <= 0:
        since = now - 10
    since_iso = datetime.fromtimestamp(since).astimezone().isoformat(timespec="seconds")
    try:
        out = docker("logs", "--since", since_iso, CONTAINER, timeout=15)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        log(f"读取 docker logs 失败：{type(e).__name__}: {e}")
        return

    # 只把包含掉线关键字的行当作事件；同一条日志 60 秒内不重复处理。
    for line in text.splitlines():
        if "KickedOffLine" not in line and "账号状态变更为离线" not in line:
            continue
        if now - float(state.get("last_kick", 0) or 0) < 60:
            continue  # KickedOffLine 与「账号状态变更为离线」通常成对出现，只处理一次
        key = line.strip()[-240:]
        if key == state.get("last_kick_key") and now - float(state.get("last_kick", 0) or 0) < 60:
            continue
        state["last_kick_key"] = key
        state["last_kick"] = now
        handle_offline("QQ 掉线", line, state, no_email=no_email, no_restart=no_restart, dry_run=dry_run)
        break


def _signal(_signum, _frame) -> None:
    global _stop
    _stop = True
    log("收到退出信号，停止看门狗")


def main() -> int:
    parser = argparse.ArgumentParser(description="NapCat/QQ 掉线看门狗")
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--no-restart", action="store_true", help="只告警，不自动重启")
    parser.add_argument("--no-email", action="store_true", help="不发邮件")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不告警不重启")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    state = load_state()
    log(f"看门狗启动：container={CONTAINER} interval={INTERVAL}s restart={not args.no_restart}")

    while not _stop:
        try:
            check_once(
                state,
                no_email=args.no_email,
                no_restart=args.no_restart,
                dry_run=args.dry_run,
            )
            state["last_check"] = time.time()
            save_state(state)
        except Exception as e:
            log(f"检查异常：{type(e).__name__}: {e}")
            save_state(state)
        if args.once:
            break
        for _ in range(INTERVAL):
            if _stop:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
