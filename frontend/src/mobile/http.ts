import { Capacitor, CapacitorHttp } from "@capacitor/core";

export interface TextResponse {
  status: number;
  text: string;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  headers?: Record<string, string>;
  body?: unknown;
  timeoutMs?: number;
}

/**
 * 外部 HTTP 请求。
 *
 * 安卓 WebView 里的 `fetch` 受 CORS 限制，抓招聘站与调模型都可能被浏览器拦下；
 * CapacitorHttp 走原生网络栈，不受 CORS 约束。Web 开发环境退回 fetch。
 */
export async function requestText(url: string, options: RequestOptions = {}): Promise<TextResponse> {
  const method = options.method ?? "GET";
  const headers = { ...(options.headers ?? {}) };
  const timeoutMs = options.timeoutMs ?? 30_000;

  if (Capacitor.isNativePlatform()) {
    const response = await CapacitorHttp.request({
      url,
      method,
      headers,
      data: options.body,
      responseType: "text",
      connectTimeout: timeoutMs,
      readTimeout: timeoutMs,
    });
    const text =
      typeof response.data === "string" ? response.data : JSON.stringify(response.data ?? "");
    return { status: response.status, text };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method,
      headers: {
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    return { status: response.status, text: await response.text() };
  } catch (error) {
    if (controller.signal.aborted) {
      throw new DOMException("timeout", "TimeoutError");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export async function requestJson<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const response = await requestText(url, options);
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`HTTP ${response.status}：${response.text.slice(0, 300)}`);
  }
  try {
    return JSON.parse(response.text) as T;
  } catch {
    throw new Error(`上游返回了非 JSON：${response.text.slice(0, 120)}`);
  }
}

const SAFE_ERROR_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  [/timeout|abort/i, "请求超时：检查网络或端点地址"],
  [/模型未配置/i, "模型未配置：请先在「配置」页填好 base_url / 模型 / API Key"],
  [
    /画像未配置|画像至少/i,
    "候选人画像未配置或不合法：请先在「配置」页填写关键词 / 方向 / 背景摘要",
  ],
  [/base_url 与模型名不能为空|API Key 不能为空/i, "请先填完 base_url、模型名与 API Key"],
  [/模型没有返回内容|模型没有给出/i, "模型没有返回有效结果，请重试"],
  [/401|unauthorized/i, "鉴权失败：请检查 API Key"],
  [/403|forbidden/i, "请求被拒绝：请检查访问权限"],
  [/404|not found/i, "接口不存在：请检查 base_url"],
  [/429|rate limit|too many/i, "请求过于频繁：请稍后重试"],
  [/\b5\d\d\b/, "上游服务出错：请稍后重试"],
  [/非 JSON|JSON/i, "上游返回了非 JSON 响应"],
  [/network|fetch|connect|ssl|tls/i, "网络连接失败：检查网络或地址"],
];

/**
 * 把任意异常收敛为固定文案。绝不回显 `error.message` / `error.stack`：
 * 本地 API 的错误会进入 HTTP 响应，原始文本可能带端点、路径或堆栈信息。
 */
export function safeErrorText(error: unknown): string {
  const named = error as { name?: unknown; message?: unknown } | null;
  if (named?.name === "TimeoutError" || named?.name === "AbortError") {
    return "请求超时：检查网络或端点地址";
  }
  const message = error instanceof Error ? error.message : String(error);
  for (const [pattern, text] of SAFE_ERROR_RULES) {
    if (pattern.test(message)) {
      return text;
    }
  }
  return "操作失败，请重试";
}

export function readableHttpError(error: unknown): string {
  return safeErrorText(error);
}
