import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import "./app/styles.css";

const container = document.getElementById("root");
if (!container) {
  // 挂载点缺失说明 index.html 与入口脱钩 —— 立即大声失败，别渲染出一片空白
  throw new Error("找不到 #root 挂载点（index.html 与 main.tsx 脱钩？）");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
