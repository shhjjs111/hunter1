# frontend/ — React SPA

> **状态：已落地**。本工程是迁移 Wave 3 之后的实际实现，与后端 `features/`/`slices/`
> 同名同序；下文为现状说明。

## 工程结构

- **栈**：Vite + React 19 + TypeScript + Tailwind CSS 4 + TanStack Query + React Router
- **目录**：
  - `src/features/` —— 与后端 `backend/src/hunter1/slices/` **同名**（认知对称：
    看任一边能推断另一边）
  - `src/app/` —— 入口 / 路由 / 布局 / 主题
  - `src/shared/` —— `api/`（生成的类型 + Query 封装）、`ui/`（设计系统原语）、
    `streaming/`（SSE 订阅原语）
- **类型来源**：`contracts/openapi.json` 生成（禁手改，见 `../contracts/README.md`）
- **开发形态**：`npm run dev`（Vite proxy `/api` → `http://127.0.0.1:8000`），
  与后端进程完全分离；交付形态由构建产物嵌入后端（单目录分发）。

## 命令

| 命令 | 用途 |
|---|---|
| `npm run dev` | 开发服务器（:5173，proxy 至 API） |
| `npm run build` | 生产构建 |
| `npm run test` | 组件/单元测试（Vitest） |
| `npm run check` | 类型检查 + eslint + 测试（进全量门禁） |
