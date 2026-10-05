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
├── crawlers/        站点适配器（一站点一文件）
├── web/             本地 Web UI（FastAPI）
└── cli.py           命令行入口
tests/               单元 + 集成（镜像 src 结构）
docs/                规划与文档
```

## 架构约束（硬性）

1. **依赖方向**：`interfaces → application → domain`；`domain` **不得** import `infrastructure`。
2. **可测试性**：`domain` / `application` 的测试**不需要网络与数据库**。
3. **TDD**：新增行为先写失败测试（RED），再实现（GREEN）。
4. **可扩展**：新增站点 / 工具 = 新增文件 + 注册，不改核心。

## 参考

- 架构与路线：[docs/DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md)
