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
  },
});
