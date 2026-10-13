# Changelog

本文件记录 Hunter1 的显著变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed

- **发布脚本错误输出统一脱敏**：`gh_publish.py` 的错误路径此前可能把令牌原样打到终端；
  现在所有输出统一过 `_redact()`，令牌不再有外泄通道。

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

[Unreleased]: https://github.com/shhjjs111/hunter1/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/shhjjs111/hunter1/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/shhjjs111/hunter1/releases/tag/v0.1.0
