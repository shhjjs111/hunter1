# 开发指引

## 环境要求

- **Python 3.12+**
- **Node.js 24**
- **uv 0.12.23**（锁文件是唯一依赖事实源）

## 搭建开发环境

```bash
# 1. 安装锁定工具
python -m pip install uv==0.12.23

# 2. 按 uv.lock 创建 backend/.venv
uv sync --project backend --frozen \
  --extra dev --extra web --extra db --extra crawler --extra build

# 3. 前端锁文件安装
cd frontend && npm ci && cd ..

# 4. 验证（与 CI 同一条命令）
PY="$(pwd)/backend/.venv/Scripts/python.exe" bash scripts/check.sh --with-pack
```

> `.tools/python` 是本机遗留的 embeddable 工具链，可继续用于临时脚本；CI、发布
> 和可复现验证必须使用 `backend/.venv`。依赖变化后执行
> `uv lock --project backend --python 3.12` 并提交 `backend/uv.lock`。
>
> 要单跑某一环，**必须先 `cd backend`**（Python 工程根不在仓库根）：
>
> ```bash
> cd backend
> .venv/Scripts/python.exe -m pytest
> .venv/Scripts/python.exe -m ruff check .
> .venv/Scripts/python.exe -m pyright --pythonpath .venv/Scripts/python.exe
> ```

## 质量门禁

一条命令跑完全部检查（与 CI 同款）：

```bash
bash scripts/check.sh
```

或分别跑（后端在 `backend/` 下）：

```bash
cd backend
<python> -m ruff format --check .   # 格式
<python> -m ruff check .            # 静态检查
<python> -m pyright --pythonpath <python>   # 类型检查（须传解释器，否则报假 import 错）
<python> -m pytest                  # 测试
```

> ⚠️ `pyproject.toml` 的 `addopts=-q` 与命令行 `-q` 叠加成 `-qq` 会吞掉汇总行：
> 要读「N passed」用 `-o addopts=` 覆盖。

前端：

```bash
cd frontend
npm run check    # tsc --noEmit + eslint + vitest
npm run lint     # 只跑 eslint
npm run build    # 产出 dist/（交付形态由后端服务它）
```

### shellcheck（可选，推荐装）

`check.sh` 会对 `scripts/*.sh` 跑 shellcheck —— `bash -n` 只查语法，shellcheck
才查语义级缺陷（未加引号的展开、错误的续行、数组误用）。本机没装时**明确提示
并跳过**（不是静默跳过），但**跳过不等于通过**：门禁以**退出码 3** 收场，
与「检查失败」的 `1` 区分开 —— `3` 是「有检查没跑」，`1` 是「查出错」。
CI 装了 shellcheck（`shellcheck-py==0.11.0.1`）所以不会红；本机没装时会看到
`== 有 N 项检查**未执行**（见上方 ⚠）—— 这不等于通过 ==`。

```bash
./.tools/python/python.exe -m pip install --index-url https://pypi.org/simple shellcheck-py
```

`check.sh` 的探测顺序是 PATH → **`$PY` 同级脚本目录**（Windows `Scripts/`、
Unix `bin/`）→ 项目自带解释器。第二项是必需的：实测 pip 把 shellcheck 放进
`.tools/python/Scripts/`，而该目录**不在** Git Bash 的 PATH 上，只靠 PATH 会
「装了却报未装」。

> ⚠️ **必须指定官方 PyPI 索引**。清华镜像未收录 `shellcheck-py` 的 win_amd64
> wheel；本机的 `PIP_INDEX_URL` 指向镜像（CI 上没有该变量，但写明更稳）。这也是
> 它**刻意不写进 `pyproject.toml` 的 `[dev]`** 的原因 —— 写进去会让
> `pip install -e ".[dev]"` 在镜像环境下直接失败（一条可选检查项不该拖垮主安装路径）。
> CI 侧**同样用 pip**，只是**钉住版本**（`shellcheck-py==0.11.0.1`）。不走 apt 是
> 因为 apt 的版本随 runner 镜像漂移 —— 实测比 0.11.0 旧，同一段 `A && B || C`
> 旧版报 SC2015、新版不报，会造成「本机绿、CI 红」。见 `.github/workflows/ci.yml`。

**提交前必须全绿。** 详见 `.github/workflows/ci.yml`。

## 目录结构（v2：前后端分离）

```
backend/
├── src/hunter1/
│   ├── platform/     机制内核（db / llm / fetch / update / text）
│   ├── slices/       业务切片（jobs crawl applications assistant scoring settings）
│   ├── domain/       共享模型（过渡期，见 docs/ARCHITECTURE.md）
│   ├── application/  端口协议（进程边界）
│   ├── main.py       组装根（挂路由 + 服务前端产物）
│   └── cli.py        命令行入口
└── tests/            镜像 src；tests/slices/<name> = 该切片独立验证
frontend/             React SPA（features/ 与 slices/ 同名）
contracts/            OpenAPI 快照（生成物，禁手改）
scripts/              check.sh / dev.sh / contracts.sh / build.py
docs/                 架构白皮书与开发指引
```

依赖方向与切片规则见 `AGENTS.md`；设计理由见 `docs/ARCHITECTURE.md`。

## 运行

```bash
bash scripts/dev.sh      # 开发形态：API(:8000) + Vite(:5173)，浏览器访问 5173
hunter1 serve            # 交付形态：单进程服务 API + 前端产物
hunter1 crawl            # 命令行跑一轮抓取
hunter1 update --source <版本清单 URL>   # 检查更新
```

开发形态下前端由 Vite 服务、经 proxy 打 `/api` 到后端；交付形态下两者同源 ——
**请求路径一致，业务代码零分支**。

数据位置由 `paths.py` 按运行形态决定，**通常不需要传 `--db`**：

| 形态 | 数据目录 |
|---|---|
| 仓库里开发 | Python 工程根（本仓 monorepo 里是 `backend/`）的 `.data/hunter1.db` |
| 打包产物 | 程序旁的 `data/hunter1.db` |
| `pip install` 后 | 平台用户数据目录（如 `%LOCALAPPDATA%\hunter1`） |

只有想换位置时才加 `--db <路径>`。

本机开发（项目自带 Python embeddable 版）：

```bash
./.tools/python/python.exe scripts/serve.py
```

> ⚠️ embeddable 版用 `python312._pth` 接管 `sys.path`，**会忽略 `PYTHONPATH`**，
> 且 editable 安装会因构建后端隔离失败。所以开发期用 `scripts/serve.py`
> （显式注入 `src` 路径，与 `examples/*.py` 同款）；pytest 走 `pyproject.toml`
> 的 `pythonpath` 配置，不受影响。

## 架构约束（硬性）

1. **依赖方向**（`tests/test_architecture.py` 逐条断言，越权即红灯）：
   `platform ← slices`；`slices` 之间只允许
   `crawl/scoring/applications/assistant → jobs`；进程边界协议从
   `hunter1.application.ports` 取。完整表格见 `docs/ARCHITECTURE.md`。
2. **可测试性**：切片测试**不需要网络与数据库**（真 SQLite + 假外部世界）。
   环境特定的能力一律经端口注入（见 `main.py` 的 `AppContext`）。
3. **TDD**：新增行为先写失败测试（RED），再实现（GREEN）。
4. **可扩展**：新增站点 = 在 `slices/crawl/sites.py` 的 `SITES` 加一条
   `SiteDefinition` + 一份 HTML 快照；新增助手工具 = 写一个带类型标注的函数并注册。
   两者都不改核心代码。
5. **不静默失败**：拿不到数据与「没有数据」必须能分辨 ——
   被风控拦截抛 `CrawlBlockedError`、配置损坏抛错而不是返回 `None`、
   模型报错要原样显示给用户。
   **流式端点尤其要注意**：响应一旦开始就没法再重定向，所以失败必须作为
   事件流里的一条 `error` 事件交出去（见 `slices/assistant/router.py` 的 `_stream_turn`）；
   而 `TextDelta`/`StreamComplete` 这套事件契约，让「真流式」与「降级后的
   一次性返回」在调用方看来是同一个接口（`degraded` 标记是哪一种）。

### 给已有用户加数据库约束（规程）

`Base.metadata.create_all()` **只对新表生效** —— 对已存在的表，它不会补加索引或
唯一约束。所以给已发布的库加约束不能只改 ORM 模型，要走三步：

1. **先修脏数据**：约束生效前库里可能已有违反它的行，直接 `CREATE UNIQUE INDEX`
   会抛裸 `IntegrityError`。而 `initialize()` 跑在 `AppContext.default` 的**启动
   路径**上 —— 那等于应用起不来，报错还是 sqlite 原始异常，用户无从自救。
2. **在 `Database.initialize()` 里建**（不是在 ORM 的 `__table_args__`）：老库也
   需要这条约束，而 `create_all` 补不上。用 `IF NOT EXISTS` 保证语句幂等。
3. **修复要留痕**：重排/清理是在**改用户数据**，不能静默。真触发时打一行提示
   （见 `initialize()` 里的 `repaired` 分支）。

参照实现：`platform/db/database.py` 的 `_repair_duplicate_message_sequences`
（`(conversation_id, sequence)` 唯一索引）—— 检测用分组 `HAVING COUNT(*) > 1`
（干净库只付一次 GROUP BY、零写入），修复**重排而非删除**（保住数据），排序键
`(sequence, created_at, id)` 保证结果确定。测试见
`tests/platform/test_conversations.py` 的 `TestRepairOfLegacyDuplicates`。

## 运维脚本

| 脚本 | 用途 |
|---|---|
| `scripts/check.sh` | 一条命令跑完后端格式/静态/类型/测试 + 脚本与示例（ruff/pyright + shellcheck + 离线冒烟）+ 前端检查与**生产构建** + 契约漂移（与 CI 同款） |
| `scripts/contracts.sh` | 导出 OpenAPI 快照与前端类型；`--check` 为漂移门禁 |
| `scripts/dev.sh` | 开发形态：API(:8000) + Vite(:5173) 双进程 |
| `scripts/build.py` | 构建打包产物 + 硬校验（前端产物可达性、体积） |
| `scripts/refresh_fixtures.py` | 站点改版后重抓并刷新离线快照（裁剪 + 回读校验后才落盘） |
| `scripts/serve.py` | 开发期启动 Web UI（不装包也能用） |
| `scripts/make_manifest.py` | 生成自更新清单（版本取包本身；拒绝平台错词；对占位符 URL 告警） |
| `scripts/gh_setup.sh` | 安全交接 GitHub 令牌：只经 stdin（不回显），写入 Git 凭据管理器，当场校验 |
| `scripts/gh_publish.py` | 遗留应急发布工具；标准发布由 tag 触发的 Release workflow 完成 |
| `scripts/release.sh` | 遗留本地发布校验/清单工具；不是 immutable release 的标准入口 |
| `release.cmd` | 遗留双击入口；只在 workflow 不可用时应急使用，并需人工补齐 SBOM/attestation |

## 打包

```bash
./.tools/python/python.exe scripts/build.py --zip
```

- 配置：`hunter1.spec`（单目录模式）。**前端构建产物必须显式带上** ——
  `main.frontend_dir()` 在打包态从 `sys._MEIPASS / "hunter1" / "web_dist"` 定位它，
  而 `.py` 被编译进归档时不会顺带带上 `frontend/dist`；目标路径与解析分支保持一致，
  这样同一行代码在开发态与打包态都成立。
- 硬校验分**四**层（`scripts/build.py`，逐层变贵、各抓不同的问题）：
  1. **静态**：缺 exe / 缺前端产物 / 缺 `VERSION` / 体积预算超限 → 不合格；
  2. **`--help`**（秒级，抓「根本起不来」）；
  3. **`--version`**（起来的是不是**这一版** —— 旧产物不认这个参数，会红）；
  4. **真起一次服务**：请求各前端路由（`/`、`/settings`、`/assistant`、`/crawl`、
     `/applications`），再**从 `index.html` 取出它引用的资源路径逐个请求** ——
     前端缺产物或文件名对不上时，页面壳能返回 200 而 JS 404，只有按引用查才抓得到。

  > 为什么必须有第二层：`--help` 走 argparse，**早于静态资源加载**。产物缺失或
  > 路径错位时它照样 exit 0，而实际起服务后页面拿不到资源。只靠静态检查会放行
  > 这种包。

- 体积预算 80MB（当前产物实测约 43MB）。原先写 200MB —— 那是实际值的近 5 倍，
  永远不会触发，等于没有门禁；收到 80MB 才有区分度。
- 冒烟用临时库、临时端口，日志写文件（不是管道：产物出错时会刷大量 traceback，
  管道缓冲区写满会让子进程阻塞在写日志上，表现为莫名的 ReadTimeout）。
- **冒烟必须覆盖「无参数启动」**（`smoke_default_args`）：便携交付形态下用户拿到的
  就是一个 exe，默认动作是**双击** —— 而双击不带任何参数。此前的冒烟全用
  `hunter1.exe serve`（显式带子命令），于是「用户最常走的那条路」从未被覆盖：
  实测无参数运行会打印帮助后 `exit 1`（黑框一闪、界面不出现），而所有冒烟全绿。
  与下面「不覆盖的路径最先烂掉」是同一条教训 —— 只测「你会怎么调用它」不够，
  还要测「用户会怎么打开它」。
  该路径固定使用默认 8000 端口；若端口已被占用，门禁会**明确失败**并提示释放，
  不会把「这条路径没验证」当成通过。
- 验收方式（发布前人工做一次）：把 `dist/hunter1/` 复制到**项目外**的目录再启动
  （那里看不到源码），确认页面能开、能配置、能抓取 —— 这是「解压即用」在本机
  可做的最强验证。自动化门禁已覆盖「起服务 + 页面渲染」这一段。
  启动方式要按**用户的方式**做：**直接双击 `hunter1.exe`**（不加参数），
  而不是敲 `hunter1.exe serve` —— 两者走的是不同代码路径。

### 数据目录

`paths.py` 是「数据放哪」的唯一决定点：开发时**工程根**（本仓 monorepo 里是
`backend/`，即 pyproject.toml 所在的那一级）的 `.data/`，打包后程序目录旁的 `data/`
（便携：解压即用、删目录即卸载）。两边都用 `sys.frozen` 判别；上溯找工程根时会止步于
虚拟环境边界（`pyvenv.cfg` / `site-packages`），免得命中**宿主项目**的 `pyproject.toml`
把数据写进别人的仓库。

### 前端与契约

打包前会先 `npm run build`（缺产物直接失败，不产出一个「没有界面」的包）。

契约改动流程：改后端 `slices/*/schemas.py` → `bash scripts/contracts.sh` →
提交更新后的 `contracts/openapi.json` 与 `frontend/src/shared/api/schema.d.ts`。
CI 用 `contracts.sh --check` 拦截漏导出。

生成器跑在 `frontend/tools/contract-codegen` 的独立依赖树里：
`openapi-typescript` 声明 peer `typescript@^5.x`，而主工程用 TS 6 —— 仍不在范围内，
生成器只产出 `.d.ts` 文本，两边编译器版本互不影响。

### 更新

`hunter1 update` 检查并下载新版本。清单格式：

```json
{
  "version": "0.2.0",
  "notes": "修了几个 bug",
  "assets": [
    {"platform": "win32", "url": "https://.../hunter1.zip",
     "sha256": "<64 位十六进制>", "size": 44040192}
  ]
}
```

- 版本比较用**数字元组**，不是字符串（`1.10.0 > 1.9.0`）。
- 下载先落 `.part`，**校验通过才改名到目标**；校验失败则临时文件与目标位置都不留痕。
- 压缩包里出现越界路径（`..` / 绝对路径）直接拒绝，不静默「修正」。
- 刻意不实现自我替换（见 README 的说明），因此没有「重启后生效」那类隐藏行为。

### ⚠️ 平台键有两套，别混用

项目里存在**两组**平台名，取值不同、用途不同：

| 来源 | 取值 | 用在哪 |
|---|---|---|
| `sys.platform` | `win32` / `darwin` / `linux` | 更新链：`cli._update` 传入、`asset_for()` 精确匹配、`build.py` 的 zip 名（`hunter1-win32.zip`） |
| `paths.py` 的 `_platform_key` | `windows` / `macos` / `linux` | 仅数据目录（`user_data_dir`）。可执行文件后缀也不在这里 —— 在 `scripts/artifact.py` 的 `exe_name`（按 `platform` **前缀**判：`win32` / `cygwin` 带 `.exe`，其余无后缀）；`build.py` 的 `_exe_name` 只是转调它 |

**manifest 的 `platform` 字段必须写 `win32`**（与 `asset_for` 的输入对齐）。
写 `windows` 不会报错，只会让「有产物却永远匹配不上」——`asset_for` 是精确匹配、
刻意不退回别的平台，于是每次检查更新都得到「本平台暂无产物」。

### 发布前检查单

正式发布由 `v*` tag 触发 `.github/workflows/release.yml`。本机只负责版本、锁文件、
变更说明和发布前验证；不要再用旧的本机上传脚本作为标准发布路径。

按序执行，逐项确认：

1. **版本号与说明**：改 `backend/src/hunter1/__init__.py` 的 `__version__`，并在
   `CHANGELOG.md` 建同名版本小节。
2. **锁文件**：`uv lock --project backend --python 3.12`，确认 `backend/uv.lock`
   随代码一起提交。
3. **全量门禁**：`PY="$(pwd)/backend/.venv/Scripts/python.exe" bash scripts/check.sh`
   全绿；CI 使用 `--with-pack`。
4. **本地打包 + 冒烟**：
   `backend/.venv/Scripts/python.exe scripts/build.py --zip`，确认 zip 不含 `data/`。
5. **本地清单检查**（仅用于更新链离线演练，不是发布资产来源）：
   ```bash
   backend/.venv/Scripts/python.exe scripts/make_manifest.py \
       --asset win32=dist/hunter1-win32.zip \
       --url-base http://127.0.0.1:<PORT> \
       --out dist/manifest.json
   ```
6. **升级演练（真旧库）**：拿一份**上个版本**的用户库副本，用新版本启动一次，
   确认三条路径 —— 这是自动化门禁覆盖不到的（测试与冒烟都用全新临时库）：
   | 场景 | 观察点 |
   |---|---|
   | 干净旧库 | 正常启动；新增的唯一索引出现；**行数不变**；无告警 |
   | 撞过号的旧库 | stderr 打出「检测到 N 个会话…已重排」；序号重排为 1..N；消息不丢 |
   | 带已移除字段的旧配置 | `GET /api/settings` 的 `broken` 为 `false`（不被判损坏） |
7. **更新链路演练**（起本地 HTTP 服务当"发布源"，无需真的上传）：
   ```bash
   # 就在产物目录里起服务（清单与 zip 都在 dist/ 下）——**这一步的清单要按这个地址
   # 重新生成一份**：`--url-base http://127.0.0.1:<PORT>`，否则清单里的下载地址指向线上。
   cd dist && <python> -m http.server <PORT> --bind 127.0.0.1
   <新版本 exe> update --source http://127.0.0.1:<PORT>/manifest.json   # 期望「已是最新」
   <旧版本 exe> update --source … --dest <dir> --download               # 期望「可更新」+ 解压
   # 把清单里 sha256 改一个字符再跑 → 期望 checksum_mismatch 且目标目录为空
   ```
   三个实测细节（省得下次卡住）：
   - `make_manifest.py` 的 `version` **取自包本身**，所以它生成的清单版本 = 当前
     代码版本，拿它当"更新源"永远得到「已是最新」、**不触发下载**。要演练下载路径，
     得手工把清单里的 `version` 抬高一位（如 `0.1.1`）—— 那是测试夹具，不是产物。
   - 校验失败时 exe 退出码为 **1**（成功为 0）。用 `cmd | head` 取 `$?` 拿到的是
     `head` 的码，别据此判断 —— 要读真实码就 `> file 2>&1; echo $?`。
   - 解压结果是**单目录**结构（`<dest>/<version>/hunter1/`），不是平铺的。
8. **多分辨率视觉验收**（必做，别省）：布局问题**单元测试抓不到** —— jsdom 没有布局
   引擎，组件测试全绿也不代表窄屏不塌。用 Edge/Chrome 无头截图逐个分辨率看：
   ```bash
   EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
   "$EDGE" --headless=new --disable-gpu --hide-scrollbars \
     --virtual-time-budget=4000 --window-size=700,900 \
     --screenshot=shot.png "http://127.0.0.1:8000/"
   ```
   > ⚠️ 页面路径以 `frontend/src/app/App.tsx` 的路由表为准：**岗位库在 `/`**，
   > 不是 `/jobs`（那条路由不存在，请求它会被渲染成「页面不存在」）。五个页面分别是
   > `/`（岗位库）、`/crawl`、`/applications`、`/assistant`、`/settings`。
   必测宽度与理由：
   | 宽度 | 对应真实场景 |
   |---|---|
   | 1440 | 常见桌面全屏（基线，确认无回归） |
   | 911 | **1366 屏 + 150% 缩放**（Windows 笔记本默认档） |
   | 700 | 175% 缩放 / 并排窗口 |
   | 390 | 极窄（窗口缩到最小） |
   每页都要看（岗位库 / 抓取 / 投递记录 / 求职助手 / 配置）。判据：文字不竖排、
   按钮不折行、关键列不被裁掉、可横向滚动的内容能滑到。
   > 加 `--virtual-time-budget=4000` 等页面加载完再截，否则会截到「加载中」。

9. **产物外置验收**：把 `dist/hunter1/` 复制到项目外目录启动，确认页面能开、能配置、能抓取。
10. **合并并推 tag**：PR squash 合并后，在确切的 `main` 提交上创建 annotated tag：
   ```bash
   git switch main
   git pull --ff-only
   git tag -a v0.2.1 -m "hunter1 0.2.1"
   git push origin v0.2.1
   ```
   Release workflow 会重新构建、跑全量门禁、生成 manifest/SBOM/checksums、
   attest，随后以 draft 上传并核对 4 个资产，最后发布 immutable release。

### 账号/仓库改名

已发布 release 是 immutable，旧 `manifest.json` 中的绝对地址不能通过“重传资产”
修复。`gh_publish.py --reupload` 只保留为 immutable 启用前的历史应急代码，不得
用于当前仓库。仓库改名后应发布新的 patch 版本；旧下载地址只能寄希望于 GitHub
301 重定向，不能作为长期兼容承诺。

> ⚠️ **镜像会改写命令行文本里的 GitHub 地址**。开了 `/mirror china` 时，
> `--url-base https://github.com/...` 会被改写成 `https://gitcode.com/gh_mirror/...`
> 再传给脚本（实测：Python 收到的 `sys.argv` 已被改写）。生成清单时会把这个
> 改写后的地址烤进去。
>
> **`git remote add` 同样中招** —— 实测
> `git config --local x "https://github.com/a/b.git"` 存进去的是
> `gitcode.com/gh_mirror/a/b.git`（只读镜像，push 必失败）。所以**别手打 remote
> 地址**：`gh_publish.py` 把地址写在**脚本文件里**（文件内容不受改写），并且会
> 检测并纠正已存错的 `origin`。
>
> 实测的改写边界（判断某条路是否安全时照此对照）：
>
> | 路径 | 是否被改写 |
> |---|---|
> | bash 命令文本里的 `github.com/...`（含 heredoc、`python -c "…"`） | ✗ 会 |
> | `git config` / `git remote` 写入的值 | ✗ 会 |
> | bash 里 `api.github.com/...` | ✓ 不会 |
> | cmd / PowerShell | ✓ 不会 |
> | Python 脚本**文件内**的字符串 | ✓ 不会 |
>
> 要发布到 GitHub 官方地址又必须手写时：先 `/mirror default`，或用 shell 变量
> 拼接（`H=github; "...https://${H}.com/..."` —— 实测能逃过改写）。

### 代理：直连被挡时脚本会自动走系统代理

**实测过这个故障**：推送三次全失败，各超时 21 秒
`Failed to connect to github.com:443`；同时 `www.baidu.com` 正常（288ms）、
系统代理 `127.0.0.1:7897` 端口在听、`curl -x` 经它访问 GitHub 得到 200。

根因：代理写在**注册表**里（`ProxyEnable=1`），浏览器会用，但 **Python urllib
不读注册表** —— 脚本只会报「连不上」，而人不会想到是代理没被用上。

`gh_publish.py` 现在自己读注册表，取值优先级：

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | `HUNTER1_NO_PROXY=1` | 逃生口，设了就完全不用代理（代理本身有问题时用） |
| 2 | `HTTPS_PROXY` / `http_proxy` 等 | 显式指定优先，便于临时换代理 |
| 3 | Windows 系统代理 | `HKCU\...\Internet Settings` 的 `ProxyEnable`/`ProxyServer` |

`ProxyServer` 两种形态都认：`host:port`，以及 IE 风格的
`http=a:1;https=b:2`（GitHub 走 https，优先取那条）。

两点讲究：
- 推送用 `git -c http.proxy=…` **一次性**传代理，不写进用户的 `.gitconfig`
  —— 那是用户的配置面，工具不该擅自改。
- 网络失败时的错误信息里带上「用了哪个代理」和「浏览器能开而这里不行多半是
  系统代理没被用上」—— 否则这个坑每次都得起一遍诊断流程。

> **用户侧的 `hunter1 update` 不受影响**：它走 httpx，而 httpx 的 `trust_env=True`
> 会调 `urllib.request.getproxies()` —— 那个函数在 Windows 上**会读注册表**
> （实测：环境变量里没有任何代理设置，`getproxies()` 仍返回
> `{'http': 'http://127.0.0.1:7897', 'https': ...}`）。所以产品本身的更新链路
> 在需要代理的环境里是通的。踩坑的只有发版脚本（它原本用裸 urllib）。

## 参考

- 架构与路线：[docs/DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md)

