#!/usr/bin/env bash
# 契约流水线 —— 导出 OpenAPI 快照（+ 前端类型，前端就位后自动纳入）。
#
#   bash scripts/contracts.sh          # 重新生成快照（模型改了就跑这个并提交）
#   bash scripts/contracts.sh --check  # 漂移门禁：重新生成后必须零 diff
#
# 判据是**内容等价性**（重新生成 vs 入库快照），不是时间戳 ——
# 模型改了没导出、快照被手改，两种都会红灯（修复路径相同：重跑导出）。
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

SNAPSHOT="$ROOT/contracts/openapi.json"
GENERATED="$ROOT/contracts/.openapi.generated.json"

"$PY" "$ROOT/scripts/export_openapi.py" --out "$GENERATED"

if [[ "${1:-}" == "--check" ]]; then
  if [[ ! -f "$SNAPSHOT" ]]; then
    echo "✗ 契约快照缺失：$SNAPSHOT（先跑 bash scripts/contracts.sh 生成）"
    rm -f "$GENERATED"
    exit 1
  fi
  if ! diff -q "$SNAPSHOT" "$GENERATED" >/dev/null; then
    echo "✗ 契约漂移：快照与重新生成不一致。"
    echo "  修复路径：跑 bash scripts/contracts.sh 并提交新快照。"
    echo "--- 差异（快照 → 重新生成）---"
    diff -u "$SNAPSHOT" "$GENERATED" | head -80 || true
    rm -f "$GENERATED"
    exit 1
  fi
  rm -f "$GENERATED"
  echo "✓ 契约零漂移"
else
  mv "$GENERATED" "$SNAPSHOT"
  echo "✓ 快照已更新：$SNAPSHOT"
fi

# 前端类型生成（前端就位后自动纳入）。
# 生成器跑在**独立的依赖树**（tools/contract-codegen）里：openapi-typescript
# 声明 peer typescript@^5.x，而主工程用 TS 7 —— 生成器只产出 .d.ts 文本，
# 两边编译器版本互不影响（见该目录 package.json 的说明）。
if [[ -f "$ROOT/frontend/package.json" ]]; then
  CODEGEN="$ROOT/frontend/tools/contract-codegen"
  API_DIR="$ROOT/frontend/src/shared/api"
  GEN_TS="$API_DIR/schema.d.ts"
  TMP_TS="$API_DIR/.schema.generated.d.ts"
  mkdir -p "$API_DIR"
  if [[ ! -d "$CODEGEN/node_modules" ]]; then
    echo "初始化契约生成器环境（首次）…"
    (cd "$CODEGEN" && npm install --silent)
  fi
  (cd "$CODEGEN" && npx openapi-typescript "$SNAPSHOT" -o "$TMP_TS")
  if [[ "${1:-}" == "--check" ]]; then
    if ! diff -q "$GEN_TS" "$TMP_TS" >/dev/null 2>&1; then
      echo "✗ 前端类型漂移：schema.d.ts 与快照生成结果不一致（重跑 bash scripts/contracts.sh）。"
      rm -f "$TMP_TS"
      exit 1
    fi
    rm -f "$TMP_TS"
    echo "✓ 前端类型零漂移"
  else
    mv "$TMP_TS" "$GEN_TS"
    echo "✓ 前端类型已生成：$GEN_TS"
  fi
fi
