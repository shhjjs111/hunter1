# contracts/ — 跨端契约

## 这里有什么

- `openapi.json` —— 后端 API 的 OpenAPI 快照（**生成物**）。
  前端类型由它生成；CI 用它做漂移门禁。

## 纪律（三条，都是防止「静默漂移」）

1. **唯一事实来源是后端 Pydantic 模型**（`backend/src/hunter1/slices/*/schemas.py`）。
   改 API 形状的正确顺序：改模型 → 跑 `bash scripts/contracts.sh` 重新导出 →
   **连快照一起提交**。
2. **禁止手改 `openapi.json`**：手改会在下次导出时被静默覆盖，
   且漂移门禁会把「模型没导出」和「快照被手改」混成同一种红灯 —— 两种都让人误判。
3. **前端生成类型禁手改**：`frontend/src/shared/api/schema.d.ts` 由
   `openapi-typescript` 生成；需要改类型时改后端模型，然后重跑导出。

## 命令

```bash
bash scripts/contracts.sh           # 导出：重新生成快照 + 前端类型
bash scripts/contracts.sh --check   # 漂移门禁：重新生成后必须零 diff
```

## 漂移门禁的判据（负向验证过）

`--check` 的做法是**重新生成**一份并与入库快照比对：

- 模型改了、快照没更新 → 红灯（提示你跑导出）；
- 快照被手改、模型没动 → 红灯（同一条修复路径：重跑导出）；
- 两者一致 → 绿灯。

这比「对比时间戳」或「人工检查」可靠：它验证的是**内容等价性**，
任何一侧的单方面改动都会被发现。
