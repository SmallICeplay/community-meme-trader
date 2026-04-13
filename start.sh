#!/usr/bin/env bash
# 启动后端
set -e
cd "$(dirname "$0")"

echo "=== Meme Trader 启动 ==="

# 检查 .env
if [ ! -f backend/../.env ]; then
  echo "⚠️  .env 文件不存在，请先复制 .env.example 并填写配置:"
  echo "   cp .env.example .env"
  echo "   然后填写 AVE_API_KEY 和 WALLET_MNEMONIC"
  exit 1
fi

echo "📦 启动后端 (FastAPI)..."
cd backend
python main.py &
BACKEND_PID=$!

cd ..
echo "🎨 启动前端 (Vite)..."
cd frontend
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ 启动完成！"
echo "   前端: http://localhost:5173"
echo "   后端API: http://localhost:8000"
echo "   API文档: http://localhost:8000/docs"
echo ""
echo "按 Ctrl+C 停止所有服务..."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
