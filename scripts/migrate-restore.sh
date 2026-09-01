#!/bin/bash
# 新服务器执行：恢复迁移包并启动。
# 用法：bash scripts/migrate-restore.sh /root/xingchao-migrate-YYYY-MM-DD.tar.gz
set -e
ARCHIVE="$1"
[ -f "$ARCHIVE" ] || { echo "用法：$0 <迁移包路径>"; exit 1; }

echo "== 1/4 检查 Docker =="
if ! command -v docker >/dev/null; then
  echo "未安装 Docker，正在安装..."
  apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2
  systemctl enable --now docker
fi
docker compose version >/dev/null || { echo "缺少 docker compose 插件（docker-compose-v2）"; exit 1; }

echo "== 2/4 小内存加固（swap）=="
if [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 2000 ] && ! swapon --show | grep -q swapfile2; then
  fallocate -l 2G /swapfile2 && chmod 600 /swapfile2 && mkswap /swapfile2 && swapon /swapfile2
  echo '/swapfile2 none swap sw 0 0' >> /etc/fstab
  echo "已创建 2G swap"
fi

echo "== 3/4 解压迁移包 =="
tar xzf "$ARCHIVE" -C /root/
cd /root/xingchao
[ -f .env ] && echo "✓ .env 配置已就位"
[ -d napcat/qq ] && echo "✓ QQ 登录会话已就位（免扫码）"
[ -d napcat/config ] && echo "✓ NapCat 配置已就位"
[ -f data/xingchao.db ] && echo "✓ 管理后台全部配置/定时任务/统计已就位"

echo "== 4/4 构建并启动 =="
docker compose up -d --build
sleep 8
docker compose ps
echo ""
echo "✅ 部署完成。验收步骤见 docs/DEPLOYMENT.md 第 5 节。"
echo "如 QQ 未自动登录：浏览器打开 http://127.0.0.1:8081/panel/qq-login-qr 扫码（需 SSH 隧道）。"
