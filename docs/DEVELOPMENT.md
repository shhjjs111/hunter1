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
> （该目录已 gitignore，不随仓库分发）。用它跑质量门禁：
>
> ```bash
> ./.tools/python/python.exe -m pytest
> ./.tools/python/python.exe -m ruff check .
> ./.tools/python/python.exe -m pyright
> ```
>
> ⚠️ 该项目内解释器是 **Python embeddable 版**，`pip install -e .`（editable 安装）
> 在它上面会因构建后端隔离失败。不影响开发——`pyproject.toml` 已配
> `pythonpath = ["src"]`，pytest 能直接找到 `src/` 下的包。

## 质量门禁

一条命令跑完全部检查（与 CI 同款）：

```bash
bash scripts/check.sh
```

或分别跑：

```bash
ruff format --check .   # 格式
ruff check .            # 静态检查
pyright                 # 类型检查
pytest                  # 测试
```

**提交前必须全绿。** 详见 `.github/workflows/ci.yml`。

## 目录结构

```
src/hunter1/
├── domain/          纯模型与规则（无 IO 依赖）
├── application/     用例编排（抓取 / 评分 / 投递 / 助手）
├── infrastructure/  外部实现（SQLite / LLM / 抓取 / 邮件）
├── crawlers/        站点适配器（声明式规格 + 注册表）
├── web/             本地 Web UI（FastAPI + 服务端模板）
└── cli.py           命令行入口
tests/               单元 + 集成（镜像 src 结构）
scripts/             质量门禁、快照刷新、开发期启动
docs/                规划与文档
```

## 运行

```bash
hunter1 serve --db .data/hunter1.db --port 8000   # Web UI
hunter1 crawl --db .data/hunter1.db               # 命令行跑一轮抓取
```

本机开发（项目自带 Python embeddable 版）：

```bash
./.tools/python/python.exe scripts/serve.py --db .data/hunter1.db
```

> ⚠️ embeddable 版用 `python312._pth` 接管 `sys.path`，**会忽略 `PYTHONPATH`**，
> 且 editable 安装会因构建后端隔离失败。所以开发期用 `scripts/serve.py`
> （显式注入 `src` 路径，与 `examples/*.py` 同款）；pytest 走 `pyproject.toml`
> 的 `pythonpath` 配置，不受影响。

## 架构约束（硬性）

1. **依赖方向**：`interfaces → application → domain`；`domain` **不得** import `infrastructure`。
   `application` 也不得 import `infrastructure`（只依赖 `application/ports.py` 里的 Protocol）。
2. **可测试性**：`domain` / `application` 的测试**不需要网络与数据库**。
   环境特定的能力一律经端口注入（见 `web/context.py` 的 `AppContext`）。
3. **TDD**：新增行为先写失败测试（RED），再实现（GREEN）。
4. **可扩展**：新增站点 = 在 `crawlers/registry.py` 的 `SITES` 加一条
   `SiteDefinition` + 一份 HTML 快照；新增助手工具 = 写一个带类型标注的函数并注册。
   两者都不改核心代码。
5. **不静默失败**：拿不到数据与「没有数据」必须能分辨 ——
   被风控拦截抛 `CrawlBlockedError`、配置损坏抛错而不是返回 `None`、
   模型报错要原样显示给用户。
   **流式端点尤其要注意**：响应一旦开始就没法再重定向，所以失败必须作为
   事件流里的一条 `error` 事件交出去（见 `web/app.py` 的 `_stream_turn`）；
   而 `TextDelta`/`StreamComplete` 这套事件契约，让「真流式」与「降级后的
   一次性返回」在调用方看来是同一个接口（`degraded` 标记是哪一种）。

## 运维脚本

| 脚本 | 用途 |
|---|---|
| `scripts/check.sh` | 一条命令跑完格式/静态/类型/测试（与 CI 同款） |
| `scripts/refresh_fixtures.py` | 站点改版后重抓并刷新离线快照（裁剪 + 回读校验后才落盘） |
| `scripts/serve.py` | 开发期启动 Web UI（不装包也能用） |

## 参考

- 架构与路线：[docs/DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md)

