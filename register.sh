#!/usr/bin/env bash
# 注册 kids-task 命令到 ~/.local/bin/
# 用法: bash register.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
TARGET="$BIN_DIR/kids-task"

mkdir -p "$BIN_DIR"

cat > "$TARGET" << 'WRAPPER'
#!/usr/bin/env bash
# kids_task 本地开发服务 — 一键启动 / 停止
# 用法: kids-task        → 启动
#       kids-task stop   → 停止

PROJECT_DIR="/home/liwenchang/project/kids_task"

case "${1:-}" in
    stop)
        echo "🛑 停止 kids_task 服务..."
        pkill -f "uvicorn api.main:app" 2>/dev/null && echo "已停止" || echo "没有运行中的服务"
        ;;
    *)
        cd "$PROJECT_DIR"
        exec bash run.sh
        ;;
esac
WRAPPER

chmod +x "$TARGET"
echo "✅ 已注册: $TARGET"
echo "   使用: kids-task        → 启动"
echo "         kids-task stop   → 停止"
