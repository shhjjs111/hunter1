import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 固定 IPv4 回环：默认的 "localhost" 在 Node 18+ 解析为 ::1（IPv6），
    // 与后端（127.0.0.1）和 curl/浏览器习惯不一致，会产生「服务明明起了却连不上」。
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // 开发形态：API 跑在独立后端进程（:8000），前端只经 /api 访问 ——
      // 与交付形态（同源）保持同一套请求路径，代码零分支。
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    coverage: {
      provider: "v8",
      // 显式 `include`（而不是默认的「测试期被 import 过的文件」）是**刻意的**：
      // 默认口径下，新加的文件只要没被任何测试 import 就根本不进分母 ——
      // 于是「新文件没有测试」这件事永远不会触发闸门，恰是覆盖率最容易漏掉的那类下滑。
      include: ["src/**/*.{ts,tsx}"],
      // 测试自身、类型声明与引导入口不算产品代码（`main.tsx` 只负责挂载）。
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/**/*.d.ts",
        "src/test-setup.ts",
        "src/main.tsx",
      ],
      // 覆盖率闸门（此前没有，覆盖率只是个存量事实）。阈值是**下限**，取自当前
      // 存量再留约 1 个点的边距：挡住真正的下滑，又不至于一次无关重构就假红。
      // 当前实测：语句 87.2 / 分支 80.7 / 函数 85.6 / 行 88.1。
      thresholds: {
        statements: 86,
        branches: 79,
        functions: 84,
        lines: 87,
      },
    },
  },
});
