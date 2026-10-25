import { Capacitor, CapacitorHttp } from "@capacitor/core";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@capacitor/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@capacitor/core")>();
  return {
    ...actual,
    Capacitor: { ...actual.Capacitor, isNativePlatform: vi.fn(() => false) },
    CapacitorHttp: { request: vi.fn() },
  };
});

import { publicResponseValue, readableHttpError, requestJson, requestText } from "./http";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
  vi.restoreAllMocks();
});

describe("外部 HTTP 适配", () => {
  it("Web 环境发出 JSON 并返回文本", async () => {
    const mock = vi.fn(async () => new Response("hello", { status: 200 }));
    globalThis.fetch = mock as typeof fetch;

    const response = await requestText("https://example.com/x", {
      method: "POST",
      body: { a: 1 },
    });
    expect(response).toEqual({ status: 200, text: "hello" });
    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBe('{"a":1}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("requestJson 解析正常响应并拒绝非 2xx / 非 JSON", async () => {
    globalThis.fetch = (async () => new Response('{"ok":true}', { status: 200 })) as typeof fetch;
    await expect(requestJson<{ ok: boolean }>("https://example.com/x")).resolves.toEqual({ ok: true });

    globalThis.fetch = (async () => new Response("nope", { status: 500 })) as typeof fetch;
    await expect(requestJson("https://example.com/x")).rejects.toThrow("HTTP 500");

    globalThis.fetch = (async () => new Response("not json", { status: 200 })) as typeof fetch;
    await expect(requestJson("https://example.com/x")).rejects.toThrow("非 JSON");
  });

  it("原生环境走 CapacitorHttp（绕过 CORS）", async () => {
    vi.mocked(Capacitor.isNativePlatform).mockReturnValue(true);
    const request = vi.mocked(CapacitorHttp.request).mockResolvedValue({
      status: 200,
      data: "native-ok",
      headers: {},
      url: "https://example.com/x",
    });

    const response = await requestText("https://example.com/x");
    expect(response).toEqual({ status: 200, text: "native-ok" });
    expect(request).toHaveBeenCalledOnce();
  });

  it("超时与普通错误变成可读文案", () => {
    expect(readableHttpError(new DOMException("late", "TimeoutError"))).toContain("请求超时");
    expect(readableHttpError(new Error("HTTP 401：bad key"))).toContain("鉴权");
    expect(readableHttpError(new Error("boom"))).toBe("操作失败，请重试");
    expect(readableHttpError("plain")).toBe("操作失败，请重试");
  });

  it("响应序列化不暴露异常堆栈或文件路径", () => {
    const error = new Error("private\n    at secret.ts:1:2");
    const shared = { message: "共享但可展示" };
    const cleaned = publicResponseValue({
      message: "可展示",
      stack: "secret stack",
      nested: { lineNumber: 9, stack: "nested secret" },
      error,
      items: [shared, shared],
    });

    expect(cleaned).toEqual({
      message: "可展示",
      nested: {},
      error: { detail: "操作失败，请重试" },
      items: [{ message: "共享但可展示" }, { message: "共享但可展示" }],
    });
    expect(JSON.stringify(cleaned)).not.toContain("secret");
  });
});
