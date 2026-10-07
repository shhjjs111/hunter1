# SLICE: settings

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

LLM 配置（base_url / 模型 / 密钥）的读写，以及一次真实的最小连通性探测。

## ⚠️ 与规划的偏离（记在案）

规划期只列了 5 个切片（jobs/crawl/scoring/assistant/applications），**没有 settings**。
但前端要经 API 配置模型 —— 否则「前后端完全分离」会留个洞：配置只能回退到
旧 SSR 表单。因此补建本切片。它满足切片标准（自含 router/store/SLICE.md、
依赖可注入、有独立验证命令），不是特例。

## 公开面

| 符号 | 用途 |
|---|---|
| `SettingsStore` | 配置读写门面 |
| `build_router` | HTTP 面工厂（注入 store 与 llm_factory） |

## 依赖

- 允许：`hunter1.platform.*`、`hunter1.domain.settings`、`hunter1.application.ports`

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings` | 读取配置（密钥只给掩码）；未配置返回 `null`；**配置损坏返回 200 + `broken=true`** |
| PUT | `/api/settings` | 保存配置；`api_key` 留空 = 保留原值；校验失败 422 带原因；**损坏时仍可覆盖修复** |
| POST | `/api/settings/test` | 真发一次最小请求探测连通性 → `ConnectionTestResponse`（**有类型**，非裸 dict） |

## 三条行为约定（都源自旧界面的教训）

1. **密钥只回显掩码** —— 响应里绝不含完整 key；
2. **空 key = 不改** —— 界面读不到原值，提交空值必须理解为「保留」，
   否则用户每改一次模型就把 key 抹掉；
3. **校验失败就地给原因** —— 422 + 可读消息，不是 500、不是静默丢弃。

## 配置损坏的处理（不可自锁）

已保存的配置不合法（数据损坏 / 旧版本遗留）时，`platform.db` 的 `get_llm()`
抛 `ValueError`（区分「没配过」与「配过但坏了」是对的）。但路由**必须接住**：

- `GET` → 200 + `SettingsView(broken=True)`（字段全带默认值，表单空白可填）。
  **不能 500** —— 配置页是用户唯一能重填自救的入口，500 等于用坏数据把他锁在门外；
  也不能静默当成「没配过」，那会让用户填过的内容无声消失。
- `PUT` → 容忍损坏的 `existing`（无原值可保留，空 key 就当空），
  让重填覆盖成为可行的修复路径。
- `/settings/test` → 200 + `ConnectionTestResponse(ok=False)` + 可读原因。与上面
  「配置不完整」保持**同一报告形状**：探测端点的承诺是「给出结论」，而不是以错误
  状态码告终 —— 前端 `useTestConnection` 在出错分支只会显示一句「探测失败」，
  把「重填即可自救」这条唯一出路埋掉。**不能 500**（此前这里漏接 `ValueError`，
  正是 500）。
- 评分、助手经组装根 `_runtime_llm()` 取配置：损坏映射为 **409**
  （状态未就绪，非服务端故障）+ 指引，不是 500。

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/settings -q
```
