#!/usr/bin/env bash
# 本地质量门禁 —— 与 CI (.github/workflows/ci.yml) 同款检查，一条命令跑完。
#
# 用法：
#   bash scripts/check.sh
#   PY=/path/to/python bash scripts/check.sh   # 显式指定解释器
#
# 覆盖范围按目录存在性**自动纳入**（迁移期友好，不需要改脚本）：
#   backend/     总是检查：ruff format / ruff check / pyright / pytest
#   scripts/     不依赖 backend/，单独检查：ruff format / ruff check
#   frontend/    有 package.json 时检查：npm run check（类型 + lint + 测试）
#   contracts/   有 openapi.json 时检查：契约漂移门禁
#
# ⚠ scripts/ 的 ruff 检查必须显式传 `--config backend/pyproject.toml`。
#   根目录没有 ruff 配置，不传就会用 ruff 的**默认规则集**（行长 88、规则集也不同），
#   与 backend/ 的判定标准不一致 —— 实测过：默认集下 E402 未启用，会建议你删掉
#   其实必需的 `# noqa: E402`（RUF100 报「无用 noqa」），按它改反而引入 E402 违规。
#
# 解释器探测顺序：$PY → 项目自带 .tools/python → PATH 上的 python。
# 这样在「系统没有 Python」的机器上，只要项目内工具链存在也能直接跑。
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

echo "== 解释器: $PY =="

echo "== 后端：格式检查 (ruff format) =="
(cd "$ROOT/backend" && "$PY" -m ruff format --check .)

echo "== 后端：静态检查 (ruff check) =="
(cd "$ROOT/backend" && "$PY" -m ruff check .)

echo "== 后端：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY")

# 工程脚本（仓库根，不属于后端包）。曾经漏在门禁之外 —— 在 scripts/ 下新增的
# 几百行 Python 完全没被 lint / 格式检查过。必须显式传配置，理由见文件头注释。
echo "== 脚本：格式检查 (ruff format) =="
"$PY" -m ruff format --check --config "$ROOT/backend/pyproject.toml" "$ROOT/scripts"

echo "== 脚本：静态检查 (ruff check) =="
"$PY" -m ruff check --config "$ROOT/backend/pyproject.toml" "$ROOT/scripts"

echo "== 后端：测试 (pytest) =="
(cd "$ROOT/backend" && "$PY" -m pytest)

if [[ -f "$ROOT/frontend/package.json" ]]; then
  echo "== 前端：类型检查 + lint + 测试 (npm run check) =="
  (cd "$ROOT/frontend" && npm run --silent check)
fi

if [[ -f "$ROOT/contracts/openapi.json" ]]; then
  echo "== 契约：漂移门禁 (contracts.sh --check) =="
  bash "$ROOT/scripts/contracts.sh" --check
fi

echo "== 全部通过 =="
