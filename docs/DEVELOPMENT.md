# 开发指引

## 环境要求

- **Python 3.12+**
- 依赖管理：`pip`（标准）或 `uv`（推荐，更快）

## 搭建开发环境

```bash
# 1. 建虚拟环境
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash)
# source .venv/bin/activate        # macOS / Linux

# 2. 装依赖（含开发工具）
pip install -e ".[dev]"

# 3. 验证
python -m pytest
```

> 本机（Windows）说明：若系统无 Python，项目内自带工具链在 `.tools/python`
> （该目录已 gitignore，不随仓库分发）。**检查统一走 `check.sh`**（自动探测解释器）：
>
> ```bash
> bash scripts/check.sh
> ```
>
> 要单跑某一环，**必须先 `cd backend`**（Python 工程根不在仓库根），且 pyright
> 必须显式 `--pythonpath` 指向项目自带解释器 —— 否则它探测不到这个 embeddable
> 环境，会把已装好的 pydantic / fastapi 全报成 `Import ... could not be resolved`
> （**假错**，不是依赖缺失）：
>
> ```bash
> cd backend
> ../.tools/python/python.exe -m pytest
> ../.tools/python/python.exe -m ruff check .
> ../.tools/python/python.exe -m pyright --pythonpath ../.tools/python/python.exe
> ```
>
> ⚠️ 该项目内解释器是 **Python embeddable 版**，`pip install -e .`（editable 安装）
> 在它上面会因构建后端隔离失败。不影响开发——`pyproject.toml` 已配
> `pythonpath = ["src", "."]`，pytest 能直接找到包。

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
| 仓库里开发 | `.data/hunter1.db` |
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
| `scripts/check.sh` | 一条命令跑完后端格式/静态/类型/测试 + 前端检查 + 契约漂移（与 CI 同款） |
| `scripts/contracts.sh` | 导出 OpenAPI 快照与前端类型；`--check` 为漂移门禁 |
| `scripts/dev.sh` | 开发形态：API(:8000) + Vite(:5173) 双进程 |
| `scripts/build.py` | 构建打包产物 + 硬校验（前端产物可达性、体积） |
| `scripts/refresh_fixtures.py` | 站点改版后重抓并刷新离线快照（裁剪 + 回读校验后才落盘） |
| `scripts/serve.py` | 开发期启动 Web UI（不装包也能用） |

## 打包

```bash
./.tools/python/python.exe scripts/build.py --zip
```

- 配置：`hunter1.spec`（单目录模式）。**前端构建产物必须显式带上** ——
  `main.frontend_dir()` 在打包态从 `sys._MEIPASS / "hunter1" / "web_dist"` 定位它，
  而 `.py` 被编译进归档时不会顺带带上 `frontend/dist`；目标路径与解析分支保持一致，
  这样同一行代码在开发态与打包态都成立。
- 硬校验分两层（`scripts/build.py`）：
  1. **静态**：缺 exe / 缺前端产物 / 超体积预算 → 不合格；
  2. **冒烟**：先跑 `--help`（秒级，抓「根本起不来」），再**真起一次服务**：
     请求各前端路由（`/`、`/settings`、`/assistant`、`/crawl`、`/applications`），
     再**从 `index.html` 取出它引用的资源路径逐个请求** —— 前端缺产物或文件名
     对不上时，页面壳能返回 200 而 JS 404，只有按引用查才抓得到。

  > 为什么必须有第二层：`--help` 走 argparse，**早于静态资源加载**。产物缺失或
  > 路径错位时它照样 exit 0，而实际起服务后页面拿不到资源。只靠静态检查会放行
  > 这种包。

- 体积预算 80MB（当前产物实测约 43MB）。原先写 200MB —— 那是实际值的近 5 倍，
  永远不会触发，等于没有门禁；收到 80MB 才有区分度。
- 冒烟用临时库、临时端口，日志写文件（不是管道：产物出错时会刷大量 traceback，
  管道缓冲区写满会让子进程阻塞在写日志上，表现为莫名的 ReadTimeout）。
- 验收方式（发布前人工做一次）：把 `dist/hunter1/` 复制到**项目外**的目录再启动
  （那里看不到源码），确认页面能开、能配置、能抓取 —— 这是「解压即用」在本机
  可做的最强验证。自动化门禁已覆盖「起服务 + 页面渲染」这一段。

### 数据目录

`paths.py` 是「数据放哪」的唯一决定点：开发时仓库根的 `.data/`，打包后
程序目录旁的 `data/`（便携：解压即用、删目录即卸载）。两边都用 `sys.frozen` 判别。

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
| `paths.py` 的 `_platform_tag` | `windows` / `macos` / `linux` | 仅数据目录与可执行文件后缀 |

**manifest 的 `platform` 字段必须写 `win32`**（与 `asset_for` 的输入对齐）。
写 `windows` 不会报错，只会让「有产物却永远匹配不上」——`asset_for` 是精确匹配、
刻意不退回别的平台，于是每次检查更新都得到「本平台暂无产物」。

### 发布前检查单

按序执行，逐项确认：

1. **版本号**：只改 `src/hunter1/__init__.py` 的 `__version__`（单一来源，有测试锁定）。
2. **全量门禁**：`bash scripts/check.sh` 全绿。
3. **打包 + 冒烟**：`./.tools/python/python.exe scripts/build.py --zip`。
4. **生成清单**：
   ```bash
   ./.tools/python/python.exe scripts/make_manifest.py \
       --asset win32=dist/hunter1-win32.zip \
       --url-base <上传后的下载地址前缀> \
       --out dist/manifest.json
   ```
   清单里 `assets[].platform` 必须用 `sys.platform` 词汇（见上文对照表）；
   脚本会对已知错词（`windows`/`macos`…）直接报错、对占位符 URL 告警。
   `version` 取自包本身，与 `--version` 不一致时拒绝生成。
5. **升级演练（真旧库）**：拿一份**上个版本**的用户库副本，用新版本启动一次，
   确认三条路径 —— 这是自动化门禁覆盖不到的（测试与冒烟都用全新临时库）：
   | 场景 | 观察点 |
   |---|---|
   | 干净旧库 | 正常启动；新增的唯一索引出现；**行数不变**；无告警 |
   | 撞过号的旧库 | stderr 打出「检测到 N 个会话…已重排」；序号重排为 1..N；消息不丢 |
   | 带已移除字段的旧配置 | `GET /api/settings` 的 `broken` 为 `false`（不被判损坏） |
6. **更新链路演练**（起本地 HTTP 服务当"发布源"，无需真的上传）：
   ```bash
   cd dist/verify/serve && <python> -m http.server <PORT> --bind 127.0.0.1
   <新版本 exe> update --source http://127.0.0.1:<PORT>/manifest.json   # 期望「已是最新」
   <旧版本 exe> update --source … --dest <dir> --download               # 期望「可更新」+ 解压
   # 把清单里 sha256 改一个字符再跑 → 期望 checksum_mismatch 且目标目录为空
   ```
7. **产物外置验收**：把 `dist/hunter1/` 复制到项目外目录启动，确认页面能开、能配置、能抓取。
8. **上传 + 打 tag**：exe + zip + manifest.json 传 Release，tag = `v` + `__version__`
   （`rules.py` 容忍 `v` 前缀）。**上传后用真实地址重新生成一次清单**——`manifest.json`
   里的 `url` 是绝对地址，上传前无法知道。

> ⚠️ **镜像会改写命令行里的 GitHub 地址**。开了 `/mirror china` 时，
> `--url-base https://github.com/...` 会被改写成 `https://gitcode.com/gh_mirror/...`
> 再传给脚本（实测：Python 收到的 `sys.argv` 已被改写）。生成清单时会把这个
> 改写后的地址烤进去。要发布到 GitHub 官方地址，生成清单前先 `/mirror default`，
> 或直接用不带 github.com 的地址。

## 参考

- 架构与路线：[docs/DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md)

