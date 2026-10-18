# GitHub 仓库治理

Hunter1 的远端仓库按以下标准维护。目标不是增加流程装饰，而是让每次变更都能
追溯到提交、通过同一套检查、并留下可复现的发布记录。

## 主分支

`main` 使用 active branch ruleset：

- 只接受 Pull Request，禁止直接 push。
- 必需状态检查：`quality (3.12)`、`Analyze (python)`、
  `Analyze (javascript-typescript)`、`Review dependency changes`。
- 要求分支在合并前与 `main` 同步。
- 要求线性历史，禁止 force-push 与删除分支。
- 合并方式只开放 squash merge；合并后自动删除远端分支。

PR 标题遵循 Conventional Commits。一个 PR 只包含一个逻辑单元，验证证据使用真实
命令输出，不写推测性结论。

当前仓库只有一个维护者，因此暂未强制独立审批和 CODEOWNERS 审批；CODEOWNERS
只负责自动请求 review。这是明确的单人维护例外，不是团队级审批。增加第二名
维护者后必须启用至少 1 个 approval、CODEOWNERS review 与 last-push approval。

## 发布与 Tag

- 版本号遵循 SemVer，并先更新 `backend/src/hunter1/__init__.py` 与
  `CHANGELOG.md`。
- Release tag 使用 annotated tag `vX.Y.Z`，指向待发布的 `main` 提交。
- `v*` tag 受 active tag ruleset 保护，禁止删除和移动。
- 已启用 immutable releases；正式发布后不得替换资产或改写说明。
- 推送 `v*` tag 会触发 `Release` workflow，在 GitHub Windows runner 上重新执行
  全量门禁和打包，禁止复用本机旧产物。
- Release 必须包含平台产物、`manifest.json`、`sbom.spdx.json` 与
  `SHA256SUMS.txt`，说明从 `CHANGELOG.md` 的对应版本小节提取。
- 发布前先在 draft release 上传并核对资产，再转正式发布；资产同时生成 GitHub
  build provenance 与 SBOM attestation。
- Windows Authenticode 证书尚未配置，因此 exe 目前不是受操作系统信任的签名
  二进制。证书接入前不得把“已签名”作为对外声明。
- 已发布版本不移动 tag；修复通过新版本发布。

## CI 与供应链

- `quality (3.12)` 在 Windows runner 上执行完整门禁，包括打包与冒烟。
- Python 依赖由提交进仓库的 `backend/uv.lock` 锁定；CI 使用固定 uv 版本和
  `uv sync --frozen`，不会在门禁中隐式升级依赖。
- CodeQL 对 Python 与 JavaScript/TypeScript 做安全扫描。
- Dependency graph 为 Dependency Review 提供依赖差异；Dependency Review 拦截
  高风险依赖变更。
- Dependabot 每周更新 Python、npm 与 GitHub Actions 依赖。
- 第三方 action 必须固定到完整 40 位 commit SHA，由测试门禁检查。
- Actions 只允许 GitHub 官方 action，并强制 SHA 固定；默认 token 仅有只读权限。
- Release workflow 使用 OIDC 生成 build provenance 与 SBOM attestation。

## 安全

- 启用私有漏洞报告、Dependabot alerts、Dependabot security updates、
  secret scanning 与 push protection。
- CI 拉取完整 Git 历史；仓库卫生守卫同时扫描全部可达提交的新增内容，
  防止删除后仍留在历史里的密钥形态或本机路径再次进入公开历史。
- 历史中只允许一个精确例外：提交
  `92803a424a195c86f17b034378af7fcc8eec9272` 的
  `backend/tests/test_repo_hygiene.py` 合成样例。该样例早于不可变 release 与
  当前保护规则；改写会违反禁止 force-push，并破坏受保护的 `v0.2.0` tag。
  例外绑定完整提交哈希与路径，任何新命中都必须修掉。
- 提交作者邮箱使用 GitHub noreply。公开历史中已经存在的真实邮箱提交由
  仓库卫生测试以完整提交哈希锁定，不再增加。
- GitHub 账号已开启 email privacy，网页编辑与 squash merge 使用 noreply。
  历史上由 GitHub 自动生成的两个 Gmail squash commit 以完整哈希精确豁免；
  后续任何真实作者邮箱提交都必须失败。
- 安全问题走 `SECURITY.md` 的私有通道，不在公开 Issue 中披露细节。
- 发布物必须经过仓库卫生门禁，运行数据、依赖目录、缓存、密钥形态与本机路径
  不得进入索引。

## 协作表面

- Issue 使用结构化模板；安全漏洞禁用公开 Issue 通道。
- PR 使用统一模板，列出动机、改动、验证、设计取舍与自查。
- 社区行为遵循 `CODE_OF_CONDUCT.md`。
- Wiki 与 Discussions 不启用；文档统一进入 `docs/`，避免信息分裂。
