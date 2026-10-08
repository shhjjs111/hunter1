#!/usr/bin/env bash
# 开发形态：并行起后端 API（:8000）与前端 Vite（:5173，proxy /api）。
#
#   bash scripts/dev.sh
#
# 这是「前后端完全分离」的开发期形态：两个进程、各自热重载；浏览器只访问
# http://127.0.0.1:5173，前端经 proxy 访问后端 —— 与交付形态（同源）保持
# 同一套请求路径，业务代码零分支（见 frontend/vite.config.ts）。
set -euo pipefail
# 让每个后台任务自成**进程组**（见 cleanup 的整组回收）。
set -m

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
  #
  # 必须先杀**整个进程组**：`npm run dev` 会再 fork 出 node/vite，只杀直接子
  # 进程会留下占着 5173 的孤儿 —— 下次启动就报端口被占（实测：进程组 + `set -m`
  # 能回收，端口随即 FREE）。进程组不存在时回退到单进程 kill。
  #
  # 用 `if` 而不是 `[[ -n ... ]] && kill ... || true`：后者是经典的
  # `A && B || C` 形态，shellcheck 的 SC2015 会就此告警（B 失败时 C 也会跑）。
  # 这里 C 是 `true`、实际无害，但**写法上写清楚**优于「靠约定无害」——
  # 且该告警在不同 shellcheck 版本间报不报不一致（实测 0.11.0 不报、apt 的旧版报），
  # 写清楚才能让本机与 CI 的判定一致。
  for pid in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    if [[ -n "$pid" ]]; then
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

echo "== 后端 API: http://127.0.0.1:8000 =="
# ⚠ 传给**原生** python 的参数必须是相对路径或宿主路径 —— MSYS 的 `/d/...` 在
#   路径转换关闭的 shell（`MSYS_NO_PATHCONV=1`）里不会被转换，Windows 版解释器
#   会去找 `D:\d\hunter1\scripts\serve.py`，后端启动即退，而错误行会被下面 Vite
#   的输出淹没（只剩前端，看起来像「后端没起来」）。同因问题见 check.sh:50-58。
#   `exec` 让这个进程组组长就是 python 本身，cleanup 的组回收照旧成立。
(cd "$ROOT" && exec "$PY" scripts/serve.py --port 8000 --no-browser) &
BACKEND_PID=$!

echo "== 前端 Vite: http://127.0.0.1:5173（浏览器访问这里）=="
(cd "$ROOT/frontend" && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "两个进程已启动；Ctrl+C 一起停止。"
wait
