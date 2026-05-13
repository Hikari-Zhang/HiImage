#!/bin/bash
# ⚠️  LEGACY / 简化版启动脚本
#
# 此脚本为轻量级启动方式，功能不完整（不包含 GPU 检测、自动安装 PyTorch 等）。
# 全新环境或需要 GPU 支持时，请使用 scripts/dev.js（功能完整，跨平台）。
#
# 功能对比：
#   dev.js  : GPU 检测 → PyTorch 安装 → 依赖安装 → 启动后端 + 前端
#   dev.sh  : 假设环境已就绪 → 直接启动后端 + 前端
#
# 如需完整功能，请使用：node scripts/dev.js

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "🚀 Starting HiImage development mode..."
echo "📁 Project root: $PROJECT_ROOT"

# ========== 后端启动 ==========
echo "⚡ Starting Backend (FastAPI on port 8787)..."

cd "$PROJECT_ROOT/backend"

# 确保虚拟环境存在
if [ ! -d "$PROJECT_ROOT/venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$PROJECT_ROOT/venv"
fi

# 确保依赖已安装
if [ ! -f "$PROJECT_ROOT/venv/bin/uvicorn" ]; then
    echo "  Installing backend dependencies..."
    "$PROJECT_ROOT/venv/bin/pip" install -r requirements.txt
fi

# 使用虚拟环境中的 uvicorn 启动后端
"$PROJECT_ROOT/venv/bin/python" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8787 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# 等待后端就绪
echo "⏳ Waiting for backend..."
for i in $(seq 1 60); do
  if curl -s http://127.0.0.1:8787/api/health > /dev/null 2>&1; then
    echo "✅ Backend ready!"
    break
  fi
  if [ $i -eq 60 ]; then
    echo "❌ Backend failed to start"
    exit 1
  fi
  sleep 0.5
done

# ========== 前端启动 ==========
echo "🖥️  Starting Frontend (Electron + React)..."
cd "$PROJECT_ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

# 退出时清理子进程
cleanup() {
  echo ""
  echo "🛑 Shutting down..."
  kill $FRONTEND_PID 2>/dev/null || true
  kill $BACKEND_PID 2>/dev/null || true
  echo "👋 Done."
}
trap cleanup EXIT INT TERM

# 等待任一进程退出
wait
