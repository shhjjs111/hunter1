# Hunter1

[![CI](https://github.com/shhjjs111/hunter1/actions/workflows/ci.yml/badge.svg)](https://github.com/shhjjs111/hunter1/actions/workflows/ci.yml)
[![CodeQL](https://github.com/shhjjs111/hunter1/actions/workflows/codeql.yml/badge.svg)](https://github.com/shhjjs111/hunter1/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](CODE_OF_CONDUCT.md)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/shhjjs111/hunter1)](https://github.com/shhjjs111/hunter1/releases/latest)

> 求职工作台 · 轻量 · Windows 便携 · 零托管 · **前后端完全分离**

填入**自己的大模型 API Key**（任意 OpenAI 兼容厂商）即可使用：在本地完成
「岗位抓取 → 匹配评分 → AI 求职助手 → 投递管理」全流程。

## 架构一览

**后端**：切片化的纯 API（FastAPI + SQLite）。
**前端**：独立的 React SPA（Vite + TypeScript）。
**两者只经 OpenAPI 契约对话**：改后端模型 → 生成契约 → 前端类型自动跟进。

```
backend/src/hunter1/
├── platform/   机制内核（db / llm / fetch / update）
├── slices/     业务切片（jobs crawl applications assistant scoring settings）
└── main.py     组装根
frontend/src/
├── features/   ★ 与后端切片同名（认知对称）
└── shared/     设计系统 / 契约客户端 / SSE 原语
contracts/      OpenAPI 快照（生成物）
```

设计公理、依赖方向、已知取舍见 **docs/ARCHITECTURE.md**；
协作规则（多 Agent 并行开发）见 **AGENTS.md**。

## 快速开始

### 直接用（无需 Python / Node）

到 [Releases](https://github.com/shhjjs111/hunter1/releases/latest) 下载
`hunter1-win32.zip`，解压后双击 `hunter1.exe` —— 会在本机 `127.0.0.1:8000`
起服务并自动打开浏览器。数据放在程序旁的 `data/`，纯绿色便携。

启动后到「配置」页填 base_url / 模型 / API Key（点「测试连接」验证），
再到「抓取」页跑一轮，岗位库就有数据了。

### 安卓独立 App（无需电脑）

`frontend/android/` 里是 Capacitor 安卓工程。安装包随
[GitHub Release](https://github.com/shhjjs111/hunter1/releases) 提供
（`hunter1-android-<version>.apk`）；也可按 `docs/ANDROID.md` 本地构建：

- 不需要电脑，不需要同一 Wi-Fi；
- 岗位、投递、画像与助手会话都存在手机本地（IndexedDB）；
- 可在同一 Wi-Fi 下与电脑端双向同步岗位、投递与画像（不搬 API Key 与会话）；
- 抓取招聘站和调用模型时才需要互联网；
- 桌面 Web 端仍走原来的 FastAPI + SQLite，两边共用同一套 React 页面。

构建步骤、签名与当前边界见 **docs/ANDROID.md**。

### 手机访问（同一 Wi-Fi，可选）

在程序目录双击 `hunter1-lan.cmd`，终端会打印手机地址和一次性访问码：

```
手机访问：http://192.168.1.5:8000
访问码：xxxxxxxxxxxx
```

手机浏览器打开地址并输入访问码即可；输入一次后这台手机会记住 30 天。
电脑关机后手机也无法访问。完整步骤与排错见 **docs/使用说明.md**。

> 这是「同一 Wi-Fi + 访问码」的信任模型，不是公网多用户服务。不要在公司、
> 咖啡店等公共网络开启，也不要把访问码截图外发。

### 开发（双进程，前后端完全分离）

```bash
python -m pip install uv==0.12.23
uv sync --project backend --frozen \
  --extra dev --extra web --extra db --extra crawler --extra build
cd frontend && npm ci && cd ..
PY="$(pwd)/backend/.venv/Scripts/python.exe" bash scripts/dev.sh
# 浏览器访问 http://127.0.0.1:5173
```

首次使用：到「配置」页填 base_url / 模型 / API Key（点「测试连接」验证），
再到「抓取」页跑一轮，岗位库就有数据了。

### 交付（单进程单目录）

```bash
./.tools/python/python.exe scripts/build.py --zip
```

产出 `dist/hunter1/`（exe + `_internal/`，含前端产物）与 `dist/hunter1-win32.zip`。
解压后直接运行，数据放在程序旁的 `data/`：

```
hunter1/hunter1.exe
hunter1/_internal/
hunter1/data/          ← 首次运行自动创建
```

构建脚本会做硬校验：**前端产物没打进去就判不合格** —— 那种包能启动，
但界面打不开（只剩裸 API）。

## 命令

| 目的 | 命令 |
|---|---|
| 全量门禁 | `bash scripts/check.sh` |
| 契约导出 / 漂移检查 | `bash scripts/contracts.sh` / `--check` |
| 开发双进程 | `bash scripts/dev.sh` |
| 后端测试 | `cd backend && <python> -m pytest` |
| 单切片测试 | `cd backend && <python> -m pytest tests/slices/jobs` |
| 前端检查 | `cd frontend && npm run check` |
| 命令行抓取 | `<python> scripts/serve.py` 之外的 `hunter1 crawl` |
| 自更新 | `hunter1 update --source <版本清单 URL>` |

## 更新

```bash
hunter1 update --source <版本清单 URL>            # 只看有没有新版
hunter1 update --source <版本清单 URL> --download # 下好并解压到 updates/<版本>/
```

也可以把地址放进环境变量，省去每次传参。**用 `releases/latest/download/` 这个
稳定地址**——它永远指向「最新 Release 的 manifest.json」，设一次终身有效，
以后每发一版只要把新的 manifest.json 放进 Release 即可：

```bash
export HUNTER1_UPDATE_SOURCE=https://github.com/<owner>/<repo>/releases/latest/download/manifest.json
hunter1 update --download
```

**为什么默认不内嵌更新源 URL**：更新链只校验 `sha256` —— 它保证**传输完整**，
不保证**来源真实**。清单被篡改就等于任意代码执行。在签名（或受保护的 tag）就位
之前，内嵌一个默认地址等于替用户引入一个静默的信任锚。所以第一版保持显式指定：
你给的地址，才走那条地址。

国内用户可以把 `--source` 指向任意镜像（清单里的 `url` 决定实际下载地址，
换镜像时重新生成清单即可）。**注意**：`asset_for()` 按 `sys.platform` 词汇
（`win32`/`darwin`/`linux`）精确匹配，清单里写别的词会静默匹配不上。

下载完成后程序**不会自我替换**（Windows 上运行中的 exe 覆盖不了自己）：把
`updates/<版本>/` 里的内容覆盖过程序目录即可，这也是便携软件的自然更新动作。

## 验证发布

Release 现在由受保护的 `v*` tag 在 GitHub Actions 中重建，并附带：

- `manifest.json`
- `sbom.spdx.json`
- `SHA256SUMS.txt`
- GitHub build provenance 与 SBOM attestation

高级用户可验证：

```bash
sha256sum -c SHA256SUMS.txt
gh attestation verify hunter1-win32.zip --repo shhjjs111/hunter1
```

当前 Windows exe **尚未配置 Authenticode 证书**，系统仍可能显示“未知发布者”。
发布完整性由 GitHub attestation、受保护 tag、manifest SHA-256 和校验和共同保证。

## 现状

| 维度 | 状态 |
|---|---|
| 后端切片 | ✅ 6 个（jobs / crawl / applications / assistant / scoring / settings） |
| 前端 SPA | ✅ 5 个页面（岗位库 / 抓取 / 投递 / 助手 / 配置） |
| 契约流水线 | ✅ 导出 + 双漂移门禁（快照 + 前端类型） |
| 测试 | 后端 + 前端全绿（含整体渲染验收） |
| 手机端 | ✅ 响应式手机外壳；`--lan` 局域网访问 + 访问码 |
| 安卓独立版 | ✅ Capacitor APK；本地 IndexedDB + 原生 HTTP；可与电脑双向同步 |
| 打包分发 | ✅ 单目录产物（含使用说明与手机启动器），前端产物嵌入；自更新链路已实测 |

## 务实说明

- **抓取的是公开列表页**，不做登录、不绕验证码、**不做自动投递**。
  站点若返回风控页，会当作**显式失败**报出来，而不是静默给一个空列表。
- **API Key 明文存在本地 SQLite**：单机单用户场景下，系统钥匙串方案会引入
  平台特有依赖、与「零托管」冲突。界面只回显掩码，日志不输出完整密钥；
  **不要分享或提交程序目录旁的 `data/`** —— 能读取数据库文件就能读取密钥。
- **默认只绑本机**：默认监听 `127.0.0.1`，并校验 `Host` 头（只允许回环，防 DNS rebinding
  把已保存的 API Key 外送）。需要手机访问时用 `--lan`：它会绑 `0.0.0.0`、自动生成
  访问码（保存在 `data/access_token`），并把本机局域网 IP 加进 Host 白名单。
  非回环监听**必然**启用访问码；没有访问码的请求会得到 401。访问码是同一 Wi-Fi
  下的共享口令，不要把 `data/` 或访问码发给别人。若用 `HUNTER1_TRUSTED_HOSTS`
  额外指定主机名，请把本机回环与局域网 IP 一并写进去。
- **助手只读**：它查岗位与投递、给建议；写操作由你在界面上确认。
- **无 no-JS 回退**：SPA 的取舍。旧版本（服务端渲染）有表单回退，v2 放弃了 ——
  换来的是前后端完全分离与更清晰的迭代边界。
- **当前只发布 Windows 包**：源码尽量保持可移植，但在 Linux/macOS 构建矩阵与
  安装包建立前，不把“跨平台”当作交付承诺。

## 与旧系统的关系

Hunter1 是对旧项目 `RecruitOps` 的重做：**迁移抓取资产与数据，替换外壳与运行时**。

- ✅ 继承：站点适配器思路、抓取资源治理、领域模型、匹配评分逻辑、助手工具面设计
- ❌ 丢弃：Electron 壳、PostgreSQL、Chromium 三重运行时（约 1.5GB）
- 🔄 替换：codex 求职助手（297MB 二进制、硬绑 DeepSeek）→ **自研轻量 agent**（任意 API 可用）

## 文档

- 📱 **[使用说明 →](docs/使用说明.md)** ← 给第一次使用的人（含手机访问与排错）
- 🤖 **[Android 构建 →](docs/ANDROID.md)** ← 安卓独立 App 的架构、构建与签名
- 📐 **[架构 →](docs/ARCHITECTURE.md)** ← 设计公理、依赖方向、已知取舍
- 🤖 **[协作宪法 →](AGENTS.md)** ← 切片地图、契约规则、门禁矩阵
- 🛠 **[开发指引 →](docs/DEVELOPMENT.md)** ← 环境、命令、打包细节
- 📄 **[历史规划 →](docs/DEVELOPMENT-PLAN.md)** ← v1 基线（已被 v2 取代）

其他：

- 📋 **[变更日志](CHANGELOG.md)** ← 各版本改了什么
- 🤝 **[贡献指南](CONTRIBUTING.md)** ← 提 PR / 报 Issue 的流程与硬性约束
- 🧭 **[GitHub 仓库治理](docs/GITHUB.md)** ← 分支保护、检查、安全与发布规则
- 🕊️ **[行为准则](CODE_OF_CONDUCT.md)** ← 社区参与与处理标准
- 🔒 **[安全策略](SECURITY.md)** ← 漏洞报告通道与威胁模型边界
- ⚖️ **[MIT License](LICENSE)**
