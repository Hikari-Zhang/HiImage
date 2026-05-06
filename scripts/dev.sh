#!/bin/bash
# ClearWaterMark 开发模式启动脚本
# 同时启动 Backend (FastAPI) 和 Frontend (Electron + React)

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "🚀 Starting ClearWaterMark development mode..."
echo "📁 Project root: $PROJECT_ROOT"

# Start backend
echo "⚡ Starting Backend (FastAPI on port 8787)..."
cd "$PROJECT_ROOT/backend"
if [ -d "../venv" ]; then
  source ../venv/bin/activate
fi
uvicorn app.main:app --reload --host 127.0.0.1 --port 8787 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "⏳ Waiting for backend..."
for i in $(seq 1 60); do
  if curl -s http://127.0.0.1:8787/api/health > /dev/null 2>&1; then
    echo "✅ Backend ready!"
    break
  fi
  sleep 0.5
done

# Start frontend
echo "🖥️  Starting Frontend (Electron + React)..."
cd "$PROJECT_ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

# Trap to cleanup on exit
cleanup() {
  echo ""
  echo "🛑 Shutting down..."
  kill $FRONTEND_PID 2>/dev/null || true
  kill $BACKEND_PID 2>/dev/null || true
  echo "👋 Done."
}
trap cleanup EXIT INT TERM

# Wait for either process to exit
wait
