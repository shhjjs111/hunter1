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

# 传给**原生工具**（python / node）的路径必须是宿主形式。
# Git Bash 的 `/d/...` 在 MSYS 路径转换**关闭**时（`MSYS_NO_PATHCONV=1`、
# `MSYS2_ARG_CONV_EXCL=*`）不会被转成 `D:\...`，Windows 会把 `/d/...` 当成
# 「当前盘符根 + \d」→ `D:\d\hunter1\...`：python 报 can't open file、npx 报
# ResolveError，整条契约门禁在该环境下必挂（而 CI 上全绿 —— 最坏的一种不一致）。
# 有 cygpath 才转，没有（Linux/CI）则原样返回。
native_path() {
  local path="$1"
  if [[ "$path" == /* ]] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$path" 2>/dev/null || printf '%s' "$path"
  else
    printf '%s' "$path"
  fi
}

SNAPSHOT="$ROOT/contracts/openapi.json"
GENERATED="$ROOT/contracts/.openapi.generated.json"

# 中断 / 失败也要清掉临时产物。两个临时文件在 .gitignore 里也各有一条兜底
# （`/contracts/.openapi.generated.json`、`/frontend/src/shared/api/.schema.generated.d.ts`），
# 但这里不能只靠 .gitignore：**漏网的是 `git status` 之外的东西** —— 生成物若被
# 半途留下，下一次 `contracts.sh --check` 会拿它跟快照比（`mv` 前的那一步），
# 而且 `.schema.generated.d.ts` 会被 `tsc` 当成本地声明文件读进编译。
# 所以两道都要：trap 负责正常退出/中断，.gitignore 负责 `kill -9` 那种 trap 跑不到的情况。
# （release.sh 与 gh_publish.py 都以「工作区干净」为硬门禁，一次 Ctrl-C 就能把发布堵死，
# 报错还是一句笼统的「工作区有未提交改动」。）
#
# `TMP_TS` 只在前端分支里赋值（见文件末尾），而 trap 在脚本退出时必然执行 ——
# `set -u` 下引用未赋值变量会直接报错，所以只能用 `${TMP_TS:-}`。
_cleanup() {
  rm -f "$GENERATED"
  if [[ -n "${TMP_TS:-}" ]]; then
    rm -f "$TMP_TS"
  fi
}
trap _cleanup EXIT

# 参数只认 `--check` 或「不带参数」。写错一个字符（`--chek`）原先会落进下面的
# else 分支 —— 那分支的动作是 `mv` 覆盖**已入库的快照**：想验漂移的人反而把漂移
# 抹平了（而且他以为自己在做只读检查）。这类「手滑即改数据」的入口必须报错。
case "${1:-}" in
  "") MODE="write" ;;
  --check) MODE="check" ;;
  *)
    echo "✗ 未知参数：$1"
    echo "  用法：bash scripts/contracts.sh          # 重新生成快照（会改文件）"
    echo "        bash scripts/contracts.sh --check  # 只验漂移，不改任何文件"
    exit 2
    ;;
esac
if [[ $# -gt 1 ]]; then
  echo "✗ 参数过多：$*（只接受一个可选的 --check）"
  exit 2
fi

"$PY" "$(native_path "$ROOT/scripts/export_openapi.py")" --out "$(native_path "$GENERATED")"

if [[ "$MODE" == "check" ]]; then
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
# 声明 peer typescript@^5.x，而主工程用 TS 6（为 typescript-eslint 从 TS 7 降下来的，
# 见 docs/ARCHITECTURE.md）—— 两边都不在 5.x 范围内；生成器只产出 .d.ts 文本，
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
  (cd "$CODEGEN" && npx openapi-typescript "$(native_path "$SNAPSHOT")" -o "$(native_path "$TMP_TS")")
  if [[ "$MODE" == "check" ]]; then
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
