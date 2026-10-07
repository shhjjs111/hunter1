# backend/ — Python API 服务

> **这个文件为什么存在**：`pyproject.toml` 的 `readme = "README.md"` 被 hatchling
> 按**项目目录**解析。仓库是 monorepo（根 README 在上一级），而 hatchling 明确
> 拒绝 `../README.md`（`Readme path must be within the project directory`），
> 所以这里必须有一份。此前它一直缺失 —— 后果是 `pip install -e ".[dev,…]"`
> 直接失败（`OSError: Readme file does not exist: README.md`），**CI 因此从未
> 通过过**（每次都在「安装后端依赖」那步死掉，后续所有检查步骤被 skip）。
>
> 与 `frontend/README.md`、`contracts/README.md` 同为「子项目各自一份」的仓库
> 惯例；不是根 README 的副本，改根 README 时不必同步这里。

## 职责

把业务能力切成**垂直切片**，经 OpenAPI 契约暴露给前端 SPA。零托管、单进程、
数据落本地 SQLite。

## 目录

```
src/hunter1/
├── platform/     机制内核（零业务）：db / llm / fetch / update / text
├── slices/       业务切片（6）：jobs / crawl / applications / assistant / scoring / settings
├── domain/       共享模型（过渡期，见 docs/ARCHITECTURE.md 的技术债）
├── application/  进程边界端口协议（ports.py）
├── main.py       组装根 —— 唯一认识所有切片的地方
└── cli.py        serve / crawl / update
```

**一个切片 = 一个领地**：切片间只经公开面（`__init__.py`）引用，禁深链内部模块。
依赖方向由 `tests/test_architecture.py` 钉死，越权即红灯。详见 `../AGENTS.md`。

## 命令

| 命令 | 用途 |
|---|---|
| `../.tools/python/python.exe -m hunter1 serve` | 启动本地 Web UI（需 `PYTHONPATH=src`，或先 `pip install -e .`） |
| `../.tools/python/python.exe -m hunter1 crawl` | 不开界面跑一轮抓取 |
| `../.tools/python/python.exe -m pytest` | 全量测试 |
| `../.tools/python/python.exe -m pytest tests/slices/<name>` | 单切片独立验证 |

子包导入：本工程是 **src 布局**，测试靠 `pyproject.toml` 的
`pythonpath = ["src", "."]` 找到包 —— 因此**不装也能跑测试**（这也是
上面那个 `readme` 缺陷能潜伏这么久的原因：本地从来没走过 `pip install`）。

## 门禁

提交前必须在仓库根跑 `bash scripts/check.sh`（后端 ruff format / ruff check /
pyright / pytest + 契约漂移 + 前端与示例检查）。**单跑后端**见上表。

> `pyproject.toml` 的 `addopts=-q` 与命令行 `-q` 叠加成 `-qq` 会吞掉汇总行：
> 要读「N passed」用 `-o addopts=` 覆盖。

## 契约

`slices/*/schemas.py` 的 Pydantic 模型是 **API 形状的唯一事实来源**；改它之后跑
`bash scripts/contracts.sh` 重新导出快照（`../contracts/openapi.json`）并提交，
否则漂移门禁红灯。路由**必须**声明 `response_model` —— 返回裸 `dict` 会让
OpenAPI 退化成 `unknown`，前端拿不到类型。
