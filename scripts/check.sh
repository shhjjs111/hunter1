#!/usr/bin/env bash
# 本地质量门禁 —— 与 CI (.github/workflows/ci.yml) 同款检查，一条命令跑完。
#
# 用法：
#   bash scripts/check.sh
#   PY=/path/to/python bash scripts/check.sh   # 显式指定解释器
#
# 覆盖范围按目录存在性**自动纳入**（迁移期友好，不需要改脚本）：
#   backend/     总是检查：ruff format / ruff check / pyright / pytest
#   scripts/     不依赖 backend/，单独检查：ruff format / ruff check / pyright
#   examples/    示例脚本：ruff format / ruff check / pyright + 离线冒烟
#   frontend/    有 package.json 时检查：npm run check（类型 + lint + 测试）+ vite build
#   contracts/   有 openapi.json 时检查：契约漂移门禁
# 另含：shell 脚本静态分析（shellcheck；未装则明确跳过）与语法（bash -n）、
#       PyInstaller 规格语法（compile）。
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
    # ⚠ 必须解析成**绝对路径**，不能留裸名 `python`。
    # pyright 的 `--pythonpath` 要的是解释器**路径**；给它裸名时它解析不到解释器，
    # 于是找不到 site-packages，把 httpx / pydantic / fastapi / bs4 等**全部**第三方
    # import 报成 `reportMissingImports`。实测（CI 环境镜像：extraPaths 只剩 "src"）：
    # 裸名 → 41 errors；绝对路径 → 0 errors。
    #
    # 本机看不见这个问题：`pyproject.toml` 的 extraPaths 里有一条指向本机
    # `.tools/python/Lib/site-packages` 的搜索路径，把解释器解析失败掩盖掉了 ——
    # 而 CI 上 `.tools/` 不在仓库里，没有这条兜底。典型的「只有 CI 才暴露」缺陷。
    PY="$(command -v python 2>/dev/null || command -v python3 2>/dev/null || echo python)"
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

# 脚本的类型检查。两处讲究：
# 1. 必须从 backend/ 跑并显式传 scripts 路径 —— 试过在 pyproject 里用
#    `include = ["src", "../scripts"]`，pyright **静默忽略**了 `..`（实测：
#    往 scripts/ 放一个类型错误文件，仍报 0 errors）。所以只能 CLI 传路径。
# 2. 依赖 backend/pyproject.toml 的 extraPaths 含 "src"，否则脚本里
#    `from hunter1 import ...` 会被报成 9 条 reportMissingImports 假错。
echo "== 脚本：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY" "$ROOT/scripts")

# 示例脚本（examples/）。它们长期在门禁之外 —— 代价是实测暴露的真实腐化：
# 端口新增方法后示例里的假实现静默失配（pyright 一开就报）、导入未排序，
# 以及 Windows GBK 控制台下打印站点标题直接崩。与 scripts/ 同款：必须显式传
# 配置与路径（示例也用 sys.path.insert 动态找包，pyright 读不懂那句）。
echo "== 示例：格式检查 (ruff format) =="
"$PY" -m ruff format --check --config "$ROOT/backend/pyproject.toml" "$ROOT/examples"

echo "== 示例：静态检查 (ruff check) =="
"$PY" -m ruff check --config "$ROOT/backend/pyproject.toml" "$ROOT/examples"

echo "== 示例：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY" "$ROOT/examples")

# 离线冒烟：不依赖网络的三份示例要**真能跑通**。静态检查抓不到运行时崩溃
# （上面那个 GBK 问题就是典型）。`demo_crawl` 需要真实站点，不在此列。
echo "== 示例：离线冒烟 =="
"$PY" "$ROOT/examples/demo_assistant.py" > /dev/null
"$PY" "$ROOT/examples/demo_persist.py" > /dev/null
"$PY" "$ROOT/examples/demo_sites.py" --offline > /dev/null

# shell 脚本静态分析。
#
# `bash -n` 只查语法；shellcheck 查语义级缺陷（未加引号的展开、错误的续行、
# 数组误用…）—— 两者互补，都跑。
#
# ⚠ 行尾必须归一化再喂给 shellcheck。仓库内容是 LF（.gitattributes 的
#   `* text=auto eol=lf`），但 Windows 工作树里常是 CRLF。shellcheck 对 CRLF
#   文件会在**每一行**报 SC1017(error)，并在续行处产生 SC2215 假阳性 ——
#   实测 344 + 6 条，全是行尾伪影，把真问题彻底淹掉（归一化后同一批脚本 0 问题）。
#   所以先 `tr -d '\r'`：检查的是**将要提交的内容**，与 CI 检出的内容一致。
#   不这么做的话，本机跑门禁会满屏红而 CI 全绿 —— 最坏的一种不一致。
#
# 未安装时**明确提示**而非静默跳过 —— 静默跳过会让「本机没装」看起来像
# 「检查通过」（这个项目已经吃过一次「假绿」的教训）。
# ⚠ 陷阱：注释若以「井号紧跟 shellcheck」开头，会被它当成**指令**解析
# （指令名非法即 SC1072/SC1073 报错）。本节第一版就踩了 —— 在注释里提到这个
# 工具时，别让那个词落在注释的最前面。
echo "== 脚本：shell 静态分析 (shellcheck) =="
# 探测顺序：PATH → `$PY` 同级脚本目录 → 项目自带解释器。
#
# 为什么不能只靠 PATH：实测本机 `command -v shellcheck` **找不到**已装好的它
# （pip 把二进制放在 .tools/python/Scripts/，而该目录不在 Git Bash 的 PATH 上）。
# 所以按 venv 布局从 `$PY` 反推脚本目录（Windows 是 `Scripts/`，Unix 是 `bin/`）——
# 这样「pip 装在某个解释器里、但该目录不在 PATH」也能被发现（venv 未激活时即如此）。
SHELLCHECK=""
for candidate in \
  "$(command -v shellcheck 2>/dev/null || true)" \
  "$(dirname "$PY")/Scripts/shellcheck.exe" \
  "$(dirname "$PY")/bin/shellcheck" \
  "$ROOT/.tools/python/Scripts/shellcheck.exe" \
  "$ROOT/.tools/python/bin/shellcheck"
do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    SHELLCHECK="$candidate"
    break
  fi
done

if [[ -n "$SHELLCHECK" ]]; then
  # 归一化到临时目录（而不是 stdin），让报错里的文件名仍可读、可点击。
  SHELLCHECK_TMP="$(mktemp -d)"
  trap 'rm -rf "$SHELLCHECK_TMP"' EXIT
  for script in "$ROOT"/scripts/*.sh; do
    name="$(basename "$script")"
    tr -d '\r' < "$script" > "$SHELLCHECK_TMP/$name"
  done
  "$SHELLCHECK" -s bash "$SHELLCHECK_TMP"/*.sh
else
  echo "  ⚠ 未安装 shellcheck，已跳过此项（不是通过 —— 是没查）。"
  echo "    安装：$PY -m pip install --index-url https://pypi.org/simple shellcheck-py"
  echo "    （必须指定官方索引：清华镜像未收录该包的 win_amd64 wheel）"
fi

echo "== 脚本：shell 语法检查 (bash -n) =="
for script in "$ROOT"/scripts/*.sh; do
  bash -n "$script"
done

# PyInstaller 规格文件。它只在发布流程里经 build.py 用到，平时无人碰 ——
# 语法错误会一直潜伏到打包当天。一行 compile 就能提前抓住。
echo "== 打包规格：语法检查 (compile) =="
"$PY" -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), 'hunter1.spec', 'exec')" \
  "$ROOT/backend/hunter1.spec"

echo "== 后端：测试 (pytest) =="
(cd "$ROOT/backend" && "$PY" -m pytest)

if [[ -f "$ROOT/frontend/package.json" ]]; then
  echo "== 前端：类型检查 + lint + 测试 (npm run check) =="
  (cd "$ROOT/frontend" && npm run --silent check)

  # 生产构建。`npm run check` 只含 typecheck + lint + test —— **不含 build**，
  # 于是 CSS/tailwind/资源路径/插件配置这类只在打包期暴露的错误，过去要等到
  # 发布流程的 build.py 才现形。这里把它拉进门禁（产物落在 gitignore 的 dist/）。
  echo "== 前端：生产构建 (vite build) =="
  (cd "$ROOT/frontend" && npm run --silent build)
fi

if [[ -f "$ROOT/contracts/openapi.json" ]]; then
  echo "== 契约：漂移门禁 (contracts.sh --check) =="
  bash "$ROOT/scripts/contracts.sh" --check
fi

echo "== 全部通过 =="
