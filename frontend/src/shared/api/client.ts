import createClient from "openapi-fetch";

import type { paths } from "./schema";

/**
 * API 客户端 —— 类型来自 `contracts/openapi.json` 生成（`schema.d.ts` 禁手改）。
 *
 * baseUrl 用**当前源**：开发期（Vite proxy）与交付期（同源）下与 `/` 语义等价，
 * 但绝对 URL 能在任何环境解析 —— `new Request(相对URL)` 在 jsdom / Node 里
 * 会直接抛 "Failed to parse URL"，测试环境首当其冲。
 *
 * fetch 显式转发而不让库在 createClient 时捕获 globalThis.fetch：
 * 测试可以按用例替换全局 fetch（vi.stubGlobal），运行时也能后挂 polyfill。
 */
function resolveBaseUrl(): string {
  if (typeof location !== "undefined" && location.origin) {
    return location.origin;
  }
  return "/";
}

export const api = createClient<paths>({
  baseUrl: resolveBaseUrl(),
  fetch: (request) => globalThis.fetch(request),
});
