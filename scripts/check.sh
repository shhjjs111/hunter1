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
#   frontend/    目录存在时检查：npm run check（类型 + lint + 测试）+ vite build
#   contracts/   目录存在时检查（快照缺失即失败）：契约漂移门禁
# 另含：shell 脚本静态分析（shellcheck；未装则本节未执行、末尾以退出码 3 报出）与
#       语法（bash -n）、PyInstaller 规格语法（compile）。
#
# ⚠ 沙箱的「批量删除守卫」会让本脚本**门禁全绿却以 1 退出**（WorkBuddy 沙箱实测；
#   普通环境没有这层守卫，本机 Hermes 运行时复现不出来）。
#   触发点是前端覆盖率收尾：vitest 的 v8 provider 一次性递归删 `frontend/coverage/.tmp`，
#   条目数超过阈值（默认 50）即被 Node 侧拦下并报
#   `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`。
#   实测对照（沙箱内、**必须后台**运行）：默认 → 1；`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=1000` → 0；
#   `CODEBUDDY_SAFE_DELETE_ENABLED=0` → 0。
#   首选只抬阈值（守卫仍开着，只放行这一处体积正常的清理）：
#     CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=1000 bash scripts/check.sh
#   注意：**前台**跑可能被自动提权到沙箱之外，此时守卫不生效、测出来一律通过（输出里
#   会出现 bypassed 字样）—— 要复现或要证伪，都得后台跑。CI（ubuntu）没有守卫，不受影响。
#
# ⚠ scripts/ 的 ruff 检查必须显式传 `--config backend/pyproject.toml`。
#   根目录没有 ruff 配置，不传就会用 ruff 的**默认规则集**（行长 88、规则集也不同），
#   与 backend/ 的判定标准不一致 —— 实测过：默认集下 E402 未启用，会建议你删掉
#   其实必需的 `# noqa: E402`（RUF100 报「无用 noqa」），按它改反而引入 E402 违规。
#   同样必须 `cd backend` 再跑：配置里的相对 `src` 是**按进程 cwd** 解析的，不切
#   目录会让 first-party 判定漂移，判定结果随你在哪个目录敲命令翻转（详见下面
#   scripts/ 那两节的注释）。
#
# 解释器探测顺序：$PY → 项目自带 .tools/python → PATH 上的 python。
# 这样在「系统没有 Python」的机器上，只要项目内工具链存在也能直接跑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 未执行的检查计数。缺失的工具（如 shellcheck）会让对应一节「跳过」——
# 那一节**没有查**，与「查过且通过」不是一回事。末尾据此决定能不能喊「全部通过」。
SKIPPED=0

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

# 传给**原生工具**的路径必须是宿主形式：Git Bash 的 `/d/...` 在 MSYS 路径转换
# 关闭时不会被转成 `D:\...`（scripts/release.sh:16-17 记录过同类事故：Windows 版
# Python 不认 /d/... → ModuleNotFoundError）。这里把 pyright 的 --pythonpath 转成
# 宿主路径；其余传给原生工具的参数一律改用**相对路径**（见下方各节）。
# 有 cygpath 才转，没有则原样 —— Linux/CI 上路径本就是宿主形式。
PY_NATIVE="$PY"
if [[ "$PY" == /* ]] && command -v cygpath >/dev/null 2>&1; then
  PY_NATIVE="$(cygpath -w "$PY" 2>/dev/null || printf '%s' "$PY")"
fi

echo "== 解释器: $PY =="

echo "== 后端：格式检查 (ruff format) =="
(cd "$ROOT/backend" && "$PY" -m ruff format --check .)

echo "== 后端：静态检查 (ruff check) =="
(cd "$ROOT/backend" && "$PY" -m ruff check .)

echo "== 后端：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY_NATIVE")

# 工程脚本（仓库根，不属于后端包）。曾经漏在门禁之外 —— 在 scripts/ 下新增的
# 几百行 Python 完全没被 lint / 格式检查过。必须显式传配置，理由见文件头注释。
#
# ⚠ 必须 `cd backend`（与上面各步一致）。ruff 把配置里的**相对路径按进程 cwd
#   解析** —— `--config` 传绝对路径也改变不了这点。实测 `--show-settings`：
#     cwd=backend → linter.src = ["<repo>/backend/src", "<repo>/backend/tests"]
#     cwd=仓库根 → linter.src = []
#   于是 first-party 判定（进而 import 分组的合法性）**随调用者所在目录翻转**：
#   同一份工作区、同一条命令，backend/ 下报 I001、仓库根下 All checks passed。
#   而 CI 恒从仓库根跑 —— 「本机红」与「CI 绿」可以同时为真，判定标准本身在漂移。
#   根治在 backend/pyproject.toml 的 `known-first-party = ["hunter1"]`（消除漂移），
#   此处钉住 cwd 是第二道保险：配置里还有别的 cwd 相对项（如 extend-exclude），
#   且这样本节才有**确定**的运行环境，不依赖调用者在哪。
echo "== 脚本：格式检查 (ruff format) =="
(cd "$ROOT/backend" && "$PY" -m ruff format --check --config pyproject.toml ../scripts)

echo "== 脚本：静态检查 (ruff check) =="
(cd "$ROOT/backend" && "$PY" -m ruff check --config pyproject.toml ../scripts)

# 脚本的类型检查。两处讲究：
# 1. 必须从 backend/ 跑并显式传 scripts 路径 —— 试过在 pyproject 里用
#    `include = ["src", "../scripts"]`，pyright **静默忽略**了 `..`（实测：
#    往 scripts/ 放一个类型错误文件，仍报 0 errors）。所以只能 CLI 传路径。
# 2. 依赖 backend/pyproject.toml 的 extraPaths 含 "src"，否则脚本里
#    `from hunter1 import ...` 会被报成 9 条 reportMissingImports 假错。
echo "== 脚本：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY_NATIVE" ../scripts)

# 示例脚本（examples/）。它们长期在门禁之外 —— 代价是实测暴露的真实腐化：
# 端口新增方法后示例里的假实现静默失配（pyright 一开就报）、导入未排序，
# 以及 Windows GBK 控制台下打印站点标题直接崩。与 scripts/ 同款：必须显式传
# 配置与路径（示例也用 sys.path.insert 动态找包，pyright 读不懂那句），
# 并且同样 `cd backend` 钉住 cwd（理由见上面 scripts/ 那两节的注释）。
echo "== 示例：格式检查 (ruff format) =="
(cd "$ROOT/backend" && "$PY" -m ruff format --check --config pyproject.toml ../examples)

echo "== 示例：静态检查 (ruff check) =="
(cd "$ROOT/backend" && "$PY" -m ruff check --config pyproject.toml ../examples)

echo "== 示例：类型检查 (pyright) =="
(cd "$ROOT/backend" && "$PY" -m pyright --pythonpath "$PY_NATIVE" ../examples)

# 离线冒烟：不依赖网络的示例要**真能跑通**。静态检查抓不到运行时崩溃
# （上面那个 GBK 问题就是典型）。
#
# `demo_crawl` 也在列 —— 它自带 `--offline`（内联 HTML，不联网）。此前以
# 「需要真实站点」为由被排除，而它恰好是**唯一已经腐化**的示例：本地 SPEC 的
# 选择器与 `slices/crawl/sites.py` 的注册表早已漂移（`div.enterprise-list-item`
# vs `.enterprise-list-item` / `.enterprise-list-title`）。没人跑它，也就没人
# 发现它坏了一年 —— 这正是「不覆盖的示例最先烂掉」的实例。
echo "== 示例：离线冒烟 =="
(cd "$ROOT" && "$PY" examples/demo_assistant.py > /dev/null)
(cd "$ROOT" && "$PY" examples/demo_crawl.py --offline > /dev/null)
(cd "$ROOT" && "$PY" examples/demo_persist.py > /dev/null)
(cd "$ROOT" && "$PY" examples/demo_sites.py --offline > /dev/null)

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
  "$(dirname "$PY")/shellcheck" \
  "$(dirname "$PY")/Scripts/shellcheck.exe" \
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
  # `|| true` 不是装饰：bash 会用 EXIT trap 里**最后一条命令**的状态覆盖脚本自身的退出码。
  # 清理一失败，一份「全部通过」的门禁就以 1 退出 —— 而它恰恰是 AGENTS.md 定的唯一
  # 提交门禁，判错方向比不判更坏。（触发条件与 `rm` 的具体实现有关：本机 `rm` 是
  # 普通二进制就没事，换成会拒绝某种路径形态的包装就会中招。）
  # `command` 用来绕过同名 shell 函数；`2>/dev/null` 是因为失败原因不值得污染输出。
  trap 'command rm -rf "$SHELLCHECK_TMP" 2>/dev/null || true' EXIT
  for script in "$ROOT"/scripts/*.sh; do
    name="$(basename "$script")"
    tr -d '\r' < "$script" > "$SHELLCHECK_TMP/$name"
  done
  # cd 进临时目录再传相对文件名：shellcheck.exe 是原生工具，传 `/tmp/...`
  # 在路径转换关闭时会被当成非法参数（与 scripts/ 那两节同因）。
  (cd "$SHELLCHECK_TMP" && "$SHELLCHECK" -s bash ./*.sh)
else
  echo "  ⚠ 未安装 shellcheck，本节**未执行**（不是通过 —— 是没查）。"
  echo "    安装：$PY -m pip install --index-url https://pypi.org/simple shellcheck-py"
  echo "    （必须指定官方索引：清华镜像未收录该包的 win_amd64 wheel）"
  SKIPPED=$((SKIPPED + 1))
fi

echo "== 脚本：shell 语法检查 (bash -n) =="
for script in "$ROOT"/scripts/*.sh; do
  bash -n "$script"
done

# PyInstaller 规格文件。它只在发布流程里经 build.py 用到，平时无人碰 ——
# 语法错误会一直潜伏到打包当天。一行 compile 就能提前抓住。
echo "== 打包规格：语法检查 (compile) =="
(cd "$ROOT" && "$PY" -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), 'hunter1.spec', 'exec')" backend/hunter1.spec)

echo "== 后端：测试 (pytest) =="
# `--cov` 让覆盖率成为**闸门**而不只是存量事实：阈值定义在
# `backend/pyproject.toml` 的 `[tool.coverage.report] fail_under`（只此一处），
# 低于它就非零退出。刻意**不**写进 `addopts` —— 那会让每次 `pytest` 都多花
# 40 秒，日常开发循环不该付这个钱。
(cd "$ROOT/backend" && "$PY" -m pytest --cov)

# 同理：判据是前端工程目录在不在，不是 package.json 在不在 —— 后者被删时
# 前端检查会静默消失（整段 npm 命令不执行），门禁假绿。目录在而工程文件缺失，
# 就该让 npm 自己报错（红），而不是安静跳过。
if [[ -d "$ROOT/frontend" ]]; then
  echo "== 前端：类型检查 + lint + 测试 (npm run check) =="
  (cd "$ROOT/frontend" && npm run --silent check)

  # 生产构建。`npm run check` 只含 typecheck + lint + test —— **不含 build**，
  # 于是 CSS/tailwind/资源路径/插件配置这类只在打包期暴露的错误，过去要等到
  # 发布流程的 build.py 才现形。这里把它拉进门禁（产物落在 gitignore 的 dist/）。
  echo "== 前端：生产构建 (vite build) =="
  (cd "$ROOT/frontend" && npm run --silent build)
fi

# 契约门禁：判据是「契约目录在不在」，不是「快照文件在不在」。
# 用 -f openapi.json 当开关有个静默失效口：删掉那个文件，整段契约检查消失，
# 脚本继续打印「== 全部通过 ==」并 exit 0 —— CI 复用本脚本，同样绿。而
# contracts.sh --check 自带的 fail-closed（快照缺失 → exit 1）从这个入口
# 永远不可达（RED 复现：契约目录在、快照缺失时判据走 else 分支）。
# 改成目录存在性：契约目录一旦建立，快照缺失就是错，由 contracts.sh 明确报出。
if [[ -d "$ROOT/contracts" ]]; then
  echo "== 契约：漂移门禁 (contracts.sh --check) =="
  bash "$ROOT/scripts/contracts.sh" --check
fi

# 有未执行的检查就**不能喊「全部通过」** —— 那正是最难受的一种假绿：
# 本机看到「全部通过」，其实有一节根本没跑（CI 装了工具、跑了，两边结论不同源）。
# 退出码 3 与「检查失败」（1）区分开：这是「没查」，不是「查出错」。
if [[ "$SKIPPED" -gt 0 ]]; then
  echo "== 有 $SKIPPED 项检查**未执行**（见上方 ⚠）—— 这不等于通过 =="
  exit 3
fi

echo "== 全部通过 =="
