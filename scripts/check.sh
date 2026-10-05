#!/usr/bin/env bash
# 本地质量门禁 —— 与 CI (.github/workflows/ci.yml) 同款检查，一条命令跑完。
#
# 用法：
#   bash scripts/check.sh
#   PY=/path/to/python bash scripts/check.sh   # 指定解释器
set -euo pipefail

PY="${PY:-python}"

echo "== 格式检查 (ruff format) =="
"$PY" -m ruff format --check .

echo "== 静态检查 (ruff check) =="
"$PY" -m ruff check .

echo "== 类型检查 (pyright) =="
"$PY" -m pyright

echo "== 测试 (pytest) =="
"$PY" -m pytest

echo "== 全部通过 =="
