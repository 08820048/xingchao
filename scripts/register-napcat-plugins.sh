#!/bin/bash
# 让 NapCat 的插件管理器加载本地红包补丁。
#
# docker-compose.yml 已把 scripts/napcat-wallet-plugin/ 只读挂载到：
#   /app/napcat/plugins/napcat-plugin-builtin
# 但 NapCat 的插件管理器默认不注册，需要在 WebUI 打开一次「插件」页，
# 或运行本脚本（等价于打开插件页）后才会扫描并加载。
#
# 用法：./scripts/register-napcat-plugins.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBUI="$ROOT/napcat/config/webui.json"
PORT="${NAPCAT_WEBUI_PORT:-6099}"

if [ ! -f "$WEBUI" ]; then
  echo "❌ 找不到 $WEBUI（NapCat 还没初始化？）" >&2
  exit 1
fi

TOKEN=$(python3 -c "import json;print(json.load(open('$WEBUI'))['token'])")
HASH=$(python3 -c "import hashlib;print(hashlib.sha256(('$TOKEN'+'.napcat').encode()).hexdigest())")

CRED=$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"hash\":\"$HASH\"}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['Credential'])")

echo "→ 注册 NapCat 插件管理器…"
curl -fsS -X POST "http://127.0.0.1:$PORT/api/Plugin/RegisterManager" \
  -H "Authorization: Bearer $CRED" || true

echo
echo "→ 当前插件列表："
curl -fsS "http://127.0.0.1:$PORT/api/Plugin/List" \
  -H "Authorization: Bearer $CRED" \
  | python3 -m json.tool

echo
echo "如果列表里没有 napcat-plugin-builtin（或状态不是 active），看："
echo "  docker logs --tail 100 xingchao-napcat | grep -iE 'plugin|wallet'"
