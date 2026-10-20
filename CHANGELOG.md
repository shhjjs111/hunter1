# Changelog

本文件记录 Hunter1 的显著变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.1] - 2026-10-20

### Added

- **安卓 APK 随 Release 分发**：GitHub Release 附带 release 签名 APK
  （`versionName=0.4.1`、`versionCode=2`），与 Windows 包同版本发布；
  APK SHA256：`53BE1A3E34A751CD7B9B3F3E6AEFBB418C9307CEFACE2849C791A4820056A3A6`。

## [0.4.0] - 2026-10-20

### Added

- **手机端**：`<768px` 时启用独立手机外壳，提供五个移动页面、底部 Dock、
  横滑翻页、详情 Sheet、会话抽屉与安全区适配。
- **手机访问**：`hunter1 serve --lan` 绑定局域网并生成访问码；手机输入一次后
  记住 30 天。非回环监听默认强制访问码，未授权请求返回 401。
- 分发包附带 `使用说明.md` 与 `hunter1-lan.cmd`，双击即可开启手机访问。
- **安卓独立 App**：Capacitor APK，数据存手机本地（IndexedDB），抓取与模型调用走原生 HTTP；不需要电脑或同一 Wi-Fi。
- **手机 ↔ 电脑同步**：同一 Wi-Fi 下可双向同步岗位、投递与候选人画像；
  按详情页 URL 对齐，时间戳较新的一方胜出。API Key 与助手会话只留本机。

### Fixed

- 手机端浅色主题的小字对比度、设置页吸底操作栏、Action Sheet 标题换行与
  会话抽屉空态。

## [0.3.2] - 2026-10-19

### Fixed

- 连续操作时 Toast 正确重新计时；多行提示不再被圆角裁切；手机端与底栏保持间距。

## [0.3.1] - 2026-10-19

### Fixed

- 岗位库行内操作的反馈改为 dock 上方的 Toast，评分结果自动滚入视口。
- 页面提示与 Toast 补齐 `alert` / `status` 语义，读屏可播报成功与失败。
- 模型端点错误只展示厂商说明并脱敏截断，不再原样吐出原始 JSON。
- 岗位行内按钮的可访问描述同时包含岗位名与公司名。
- SPA 路由支持 `HEAD`，健康检查与链接预览不再误报 405。
- 浅色主题提高按钮填充与描边对比度。

## [0.3.0] - 2026-10-19

### Added

- **空间舞台界面**：五个业务模块统一为 visionOS 式玻璃窗口，桌面端提供环境全景、
  侧边空间小窗和底部 dock，手机端使用底部标签栏与紧凑主窗口。
- **深色 / 浅色主题**：首次访问跟随系统偏好，手动选择写入 `localStorage`；
  支持 View Transition 圆形揭幕，并按 `prefers-reduced-motion` 自动降级。
- **语义设计令牌**：新增玻璃、内凹、悬浮、状态色和模块色令牌，页面不再散落写死颜色。
- **响应式岗位表**：窄屏保留可比较的表格结构，支持横向滚动和两端渐隐提示。

### Changed

- 统一岗位库、抓取、投递记录、求职助手和配置页面的字号、间距、按钮与卡片层级。
- 环境背景按模块色调和导航方向平移，宽屏侧窗按弧线排列，页面切换保持空间连续性。

### Compatibility

- View Transition 与 CSS `linear()` 不支持的浏览器会自动退化为无转场切换，功能不受影响。

## [0.2.2] - 2026-10-18

### Fixed

- **Release workflow 在 Windows cp1252 控制台崩溃**：SBOM 脚本输出全角冒号时触发
  `UnicodeEncodeError`。发布脚本现在显式切换 UTF-8，workflow 同时设置
  `PYTHONUTF8=1`。
- `v0.2.1` tag 已创建，但 workflow 在发布前失败，因此没有对应 GitHub Release；
  `v0.2.2` 是首个包含完整发布硬化产物的版本。

## [0.2.1] - 2026-10-18

### Added

- **发布链迁移到 GitHub Actions**：`v*` tag 在 Windows runner 重新构建并执行完整
  门禁，生成 manifest、SPDX SBOM、SHA-256 校验和、build provenance 与 SBOM
  attestation，先上传 draft 核对再发布 immutable release。
- **可复现 Python 依赖**：提交 `backend/uv.lock`，CI 使用固定 uv 版本和
  `uv sync --frozen`，不再在门禁中隐式解析最新依赖。
- **GitHub 仓库治理达到可审计的团队标准**：受保护主分支与 release tag、PR-only
  合并、要求 `quality (3.12)`、线性历史；CodeQL、Dependency Review、Dependabot
  与私有漏洞报告；全部第三方 action 固定到完整 commit SHA。发布说明已按
  CHANGELOG 重写，并启用 immutable releases。
- **安全门禁全部阻断合并**：`quality (3.12)`、Python/JavaScript TypeScript
  CodeQL 分析与 Dependency Review 都是 `main` 的必需状态检查。
- **社区健康文件补齐**：新增 Contributor Covenant 2.1 行为准则、CODEOWNERS，
  并补充仓库治理说明。

### Fixed

- **安全边界加固**：SSE 错误帧在所有解析分支统一脱敏；写入端点增加跨站来源校验；
  所有响应补点击劫持与 MIME 嗅探防护头；抓取器拒绝私网/回环目标并保持 Cookie
  按原始站点隔离；JD 增加 20 万字符存储上限。
- **发布包不再包含运行数据**：默认启动冒烟使用临时数据目录，正式 zip 显式排除
  `data/`；POSIX 上新库目录/数据库/WAL/SHM 收紧到 `700/600`。
- **发布说明与文档声明校准**：明确当前只交付 Windows 包、Authenticode 尚未配置，
  移除不可用的私有联系与 Discussions 指引。
- **投递记录改为真分页**：超过一页的历史记录不再因固定 200 条上限永久不可达。
- **分页兼容与末页处理**：保留 `has_more` 兼容字段；删除末页唯一记录后自动回退。
- **API 文档与安全头兼容**：`/api/docs` 使用专用 CSP，Swagger UI 仍可加载。
- **开发端口固定**：Vite 端口被占用时明确失败，不再切到会被写请求校验拒绝的端口。
- **robots.txt 请求与页面请求使用同一主机固定 UA，并走同一限流器**。
- **抓取重试会在多个已校验公网地址之间轮换**，首个 A 记录故障不再耗尽全部重试。
- **打包冒烟不再假绿**：无参数启动所需的 8000 端口被占用时明确失败并提示释放端口。
- **`find_release` 看不到草稿 → 建出孤儿 Release**：`GET /releases/tags/<tag>` 对草稿
  返回 404，而 `ensure_release` 的「先查后建」以「查得到同名 Release」为前提。一次
  上传中断在远端留下草稿后重跑，查不到它 → `POST` 出**第二个**同 tag 的 Release，
  第一个沦为孤儿草稿（无附件、`untagged-` 网址、只增不减，需人工 `DELETE` 收尾）。
  现在 tags 端点未命中时回退查列表（带认证的列表含草稿），按 `tag_name` 匹配；
  已发布的那条仍走 tags 端点，不多付请求。
  > 影响面仅限发布工具链（`scripts/gh_publish.py`，不随产物分发）；实测于
  > 2026-10-13 发 v0.2.0，见 `TestFindReleaseSeesDrafts`。

## [0.2.0] - 2026-10-13

抓取层新增详情页正文回填，并修掉一批「静默失败」类缺陷。

### Added

- **详情页解析**：`parse_detail_page` + `fetch_detail`，为 4 个可用站点提供详情选择器。
- **抓取时回填 JD 正文**：per-job 隔离 + 状态推进 + 请求量上限。

### Fixed

- **BOSS直聘站点移除**：结构性风控使其不可用。
- **噪声信号 `geetest` 移除**：正常页引用该组件会被误判为拦截。
- **未声明 `detail_selector` 的站点不再产生假失败**。
- **压缩站点双重解压失败**：`platform/fetch` 剥离描述原始体的响应头。
- **上游失败文案**：assistant 走 `describe_llm_error`，不再吐裸机器码。
- **发布脚本错误输出统一脱敏**：`gh_publish.py` 的错误路径此前可能把令牌原样打到终端；
  现在所有输出统一过 `_redact()`，令牌不再有外泄通道。
- **卫生守卫自校准**：样本改为运行时拼装，修「提交前绿、提交后红」。

### Documentation

- crawl 切片：应届生的取舍补上量化依据（可抓 2/80 ≈ 2.5%），并修正「详情页不可用」的表述。
- 补齐开源项目的对外门面（LICENSE / CHANGELOG / SECURITY / CONTRIBUTING / 模板 / 徽章）。
- 泛化注释里的本机路径 —— 公开仓库不留开发机目录结构。

### Tests

- 上传卫生守卫：把「不要什么都传」变成会红的门禁。
- crawl 详情页快照与回读用例 —— 让详情选择器失效时先红。

## [0.1.1] - 2026-10-13

修复双击 exe 不启动的缺陷，并加固发布链。

### Fixed

- **双击 exe 无参数启动**：默认进入 `serve`，不再打印帮助后退出。
- **发布链支持移动已存在的 tag**：不再因重复 tag 建出重复 Release。
- **`find_release` 调用参数错位**：令牌曾被当成 tag 名拼进请求 URL。

### Documentation

- `docs/DEVELOPMENT.md`：打包冒烟与产物验收必须按「用户的方式」启动（模拟双击）。
- `scripts/release.sh`：tag 修复提示补上远端后果说明。

## [0.1.0] - 2026-10-13

首个公开版本。前后端完全分离的求职工作台：本地完成「岗位抓取 → 匹配评分 →
AI 求职助手 → 投递管理」全流程。

### Added

- **后端**（FastAPI + SQLite）：按能力垂直切分为 6 个切片
  （`jobs` / `crawl` / `applications` / `assistant` / `scoring` / `settings`），
  依赖方向由架构测试钉死。
- **前端**（React SPA + Vite + TypeScript）：5 个页面，feature 目录与后端切片同名。
- **契约流水线**：后端 Pydantic 模型为 API 形状唯一事实来源，导出 OpenAPI 快照 +
  生成前端类型，双向漂移门禁 + CI 拦截。
- **抓取层**：站点适配器、限流 / 重试 / robots 治理、按主机固定 UA、
  重定向逐跳治理。
- **LLM 层**：任意 OpenAI 兼容厂商，SSE 流式输出，错误文案可行动化。
- **自更新链路**：版本清单 → 下载 → `sha256` 校验 → 解压（不自我替换）。
- **打包分发**：PyInstaller 单目录产物，前端产物嵌入；`check.sh --with-pack`
  真打包 + 冒烟纳入 CI 门禁。

[Unreleased]: https://github.com/shhjjs111/hunter1/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/shhjjs111/hunter1/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/shhjjs111/hunter1/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/shhjjs111/hunter1/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/shhjjs111/hunter1/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/shhjjs111/hunter1/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/shhjjs111/hunter1/releases/tag/v0.1.0
