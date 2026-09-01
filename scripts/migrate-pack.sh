#!/bin/bash
# 旧服务器执行：打包全部状态（配置/会话/数据），用于迁移到新服务器。
# 产物：/root/xingchao-migrate.tar.gz
set -e
cd "$(dirname "$0")/.."   # 项目根目录

OUT="/root/xingchao-migrate-$(date +%F).tar.gz"
tar czf "$OUT" \
  --exclude='web/node_modules' \
  --exclude='web/dist' \
  --exclude='.git' \
  --exclude='data/logs' \
  --exclude='napcat/cache' \
  xingchao

echo "✅ 打包完成：$OUT"
echo "包含：.env / data（配置+词库+统计+定时任务）/ napcat（QQ会话+WS配置）"
echo "排除：聊天日志（如需保留历史日志，去掉 --exclude='data/logs' 重新打包）"
echo "下一步：scp $OUT root@新服务器IP:/root/"
