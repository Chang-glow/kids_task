#!/usr/bin/env bash
# kids_task 本地开发启动脚本
# 用法: ./run.sh              → 默认端口 8001，热重载
#       PORT=9000 ./run.sh    → 指定端口

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- 加载 .env.local（如果存在）----
if [ -f .env.local ]; then
    set -a
    source .env.local
    set +a
fi

PORT="${PORT:-8001}"
HOST="${HOST:-127.0.0.1}"

echo "🚀 启动 kids_task 服务..."
echo "   地址: http://${HOST}:${PORT}"
if [ -n "${DATABASE_URL:-}" ]; then
    echo "   数据库: 已配置"
else
    echo "   数据库: 未配置"
fi
echo ""

exec uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
