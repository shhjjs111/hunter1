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

export function readableHttpError(error: unknown): string {
  const named = error as { name?: unknown; message?: unknown } | null;
  if (named?.name === "TimeoutError" || named?.name === "AbortError") {
    return "请求超时：检查网络或端点地址";
  }
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof named?.message === "string") {
    return named.message;
  }
  return String(error);
}