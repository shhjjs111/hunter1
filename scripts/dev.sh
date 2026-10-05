#!/usr/bin/env bash
# 开发形态：并行起后端 API（:8000）与前端 Vite（:5173，proxy /api）。
#
#   bash scripts/dev.sh
#
# 这是「前后端完全分离」的开发期形态：两个进程、各自热重载；浏览器只访问
# http://127.0.0.1:5173，前端经 proxy 访问后端 —— 与交付形态（同源）保持
# 同一套请求路径，业务代码零分支（见 frontend/vite.config.ts）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.tools/python/python.exe" ]]; then
    PY="$ROOT/.tools/python/python.exe"
  elif [[ -x "$ROOT/.tools/python/bin/python3" ]]; then
    PY="$ROOT/.tools/python/bin/python3"
  else
    PY="python"
  fi
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "前端依赖未装：先跑 (cd frontend && npm install)"
  exit 1
fi

cleanup() {
  # 两个子进程一起收 —— 留一个在后台占着端口是本地开发最常见的困惑源
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "== 后端 API: http://127.0.0.1:8000 =="
"$PY" "$ROOT/scripts/serve.py" --port 8000 --no-browser &
BACKEND_PID=$!

echo "== 前端 Vite: http://127.0.0.1:5173（浏览器访问这里）=="
(cd "$ROOT/frontend" && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "两个进程已启动；Ctrl+C 一起停止。"
wait
