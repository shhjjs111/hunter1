import createClient, { type Middleware } from "openapi-fetch";

import { CLIENT_AUTHORED_ERROR_HEADER } from "./errors";
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
export function resolveBaseUrl(): string {
  if (typeof location !== "undefined" && location.origin) {
    return location.origin;
  }
  return "/";
}

/** 拼一个绝对 API URL —— SSE 等**绕过 openapi-fetch** 的调用方复用同一份 baseUrl。 */
export function apiUrl(path: string): string {
  return `${resolveBaseUrl().replace(/\/+$/, "")}${path}`;
}

/** 2xx 但不是 JSON 时给的状态码（语义是「上游给的东西用不了」）。 */
const UNUSABLE_RESPONSE_STATUS = 502;

function jsonErrorResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: {
      "Content-Type": "application/json",
      // 标记「这条 detail 是**我们**写给用户的」：502 属 5xx，而 `errors.ts` 按
      // 「5xx 的 detail 是服务端内部消息」的规则会丢掉它 —— 标记头让它放行，
      // 否则漏斗写好的中文原因在所有调用点被顶成「操作失败（HTTP 502）」。
      [CLIENT_AUTHORED_ERROR_HEADER]: "1",
    },
  });
}

/**
 * 错误漏斗 —— 把两类**绕过各 feature 错误分支**的失败变成可读的原因。
 *
 * 为什么必须在客户端层做：各 feature 的写法是
 * `if (error || !data) throw new Error(apiErrorMessage(error, …))`，
 * 它只处理「库正常返回了错误」。而下面两类根本不走那条路：
 *
 * 1. **fetch reject**（后端没起、连接被拒、离线、CORS）：`openapi-fetch` 把原始
 *    异常原样抛出（见其 `errorAfterMiddleware`），页面渲染出来的是英文
 *    "Failed to fetch" —— 用户不知道该干什么。这里换成一条中文错误再抛，
 *    react-query 照样把它交给页面（各页渲染 `(query.error as Error).message`）。
 * 2. **2xx 但不是 JSON**（开发期 Vite 代理把 `/api/*` 回落成页面、或前面挡了
 *    一层静态服务器）：库在 `getResponseData()` 里 `JSON.parse` 抛 SyntaxError，
 *    同样没人接。这里换成一条标准的失败响应（`{detail}` + 502），于是既有的
 *    `if (error || !data)` 分支照常工作。
 */
export const errorFunnel: Middleware = {
  onError({ error }) {
    const reason = error instanceof Error ? error.message : String(error);
    // 返回 Error（而不是造一个 Response）是这里最省事也最诚实的做法：
    // 网络层失败本来就不是 HTTP 响应，编一个状态码只会骗下游的判断。
    return new Error(`无法连接后端：${reason}。请确认 Hunter1 服务还在运行。`);
  },
  onResponse({ response }) {
    // 只有「2xx 且明确带了非 JSON 类型」才拦：204/304 没有正文（DELETE 投递就
    // 是 204），缺 Content-Type 的响应交给库自己去 parse（拦错会把好响应改坏）。
    if (!response.ok || response.status === 204 || response.status === 304) {
      return undefined;
    }
    const contentType = response.headers.get("Content-Type");
    if (!contentType || contentType.includes("json")) {
      return undefined;
    }
    return jsonErrorResponse(
      UNUSABLE_RESPONSE_STATUS,
      `后端返回了非 JSON 响应（HTTP ${response.status}，Content-Type: ${contentType}）。` +
        "通常在开发期后端没起来、请求被代理回落成了页面时出现。",
    );
  },
};

// 注意顺序：`errorFunnel` 必须在 `api` **之前**定义 —— 它是 `const`，
// 提前引用会踩 TDZ（`Cannot access 'errorFunnel' before initialization`）。
//
// 中间件用 `client.use(...)` 注册：openapi-fetch 0.17 的 `ClientOptions` 里没有
// `middleware` 字段（请求级 `RequestOptions.middleware` 是另一回事），全局中间件
// 只能这样挂 —— 写成第二个参数会被静默忽略（漏斗就此失效，报错退回英文原文）。
function createApiClient() {
  const client = createClient<paths>({
    baseUrl: resolveBaseUrl(),
    fetch: (request) => globalThis.fetch(request),
  });
  client.use(errorFunnel);
  return client;
}

export const api = createApiClient();
