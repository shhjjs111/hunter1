#!/usr/bin/env bash
# 本地质量门禁 —— 与 CI (.github/workflows/ci.yml) 同款检查，一条命令跑完。
#
# 用法：
#   bash scripts/check.sh
#   PY=/path/to/python bash scripts/check.sh   # 显式指定解释器
#
# 解释器探测顺序：$PY → 项目自带 .tools/python → PATH 上的 python。
# 这样在「系统没有 Python」的机器上，只要项目内工具链存在也能直接跑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.tools/python/python.exe" ]]; then
    PY="$ROOT/.tools/python/python.exe"
  elif [[ -x "$ROOT/.tools/python/bin/python3" ]]; then
    PY="$ROOT/.tools/python/bin/python3"
  else
    PY="python"
  fi
fi

echo "== 解释器: $PY =="

echo "== 格式检查 (ruff format) =="
"$PY" -m ruff format --check .

echo "== 静态检查 (ruff check) =="
"$PY" -m ruff check .

echo "== 类型检查 (pyright) =="
"$PY" -m pyright --pythonpath "$PY"

echo "== 测试 (pytest) =="
"$PY" -m pytest

echo "== 全部通过 =="
