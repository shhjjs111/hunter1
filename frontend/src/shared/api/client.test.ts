import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";
import { apiErrorMessage } from "./errors";

/**
 * 错误漏斗的验收 —— 两条**绕过各 feature 错误分支**的路径。
 *
 * 各 feature 统一写 `if (error || !data) throw new Error(apiErrorMessage(error, …))`，
 * 它只在「库正常返回了错误」时生效。而 fetch reject 是**抛**出去的、2xx 非 JSON 是
 * 在库内部 `JSON.parse` 抛的 —— 两条都不进那个分支，页面只能显示浏览器原文
 * （英文 "Failed to fetch"）。这里断言它们都变成了可读原因。
 */

function stubFetch(impl: () => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn(async () => impl()));
}

describe("api 客户端错误漏斗", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("网络层失败 → 抛出可读中文错误（而不是英文 Failed to fetch）", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });

    // 页面渲染的是 query.error.message（JobsPage 等），所以这里断言抛出的消息本身。
    await expect(api.GET("/api/settings")).rejects.toThrow(/无法连接后端/);
    await expect(api.GET("/api/settings")).rejects.toThrow(/Failed to fetch/); // 原始原因保留
  });

  it("2xx 但不是 JSON（代理回落成页面）→ 变成可读的 502", async () => {
    stubFetch(
      () =>
        new Response("<!doctype html><html>…</html>", {
          status: 200,
          headers: { "Content-Type": "text/html; charset=utf-8" },
        }),
    );

    const { error, response } = await api.GET("/api/settings");

    expect(response.status).toBe(502);
    // 契约里 /api/settings 只声明了 200，`error` 的类型是 undefined —— 拿到的是
    // 漏斗合成的 JSON 体，这里按运行时形状读（先过 unknown）。
    expect((error as unknown as { detail?: string }).detail).toContain("非 JSON");
    // 漏斗写好的中文原因必须真的到达用户 —— 502 属 5xx，但它的 detail 是可读的，
    // 不该被 errors.ts 用「加载配置失败（HTTP 502）」顶掉（两个模块曾互相抵消）。
    expect(apiErrorMessage(error, "加载配置失败", response)).toContain("非 JSON");
  });

  it("正常 JSON 响应不受漏斗影响", async () => {
    stubFetch(
      () =>
        new Response(JSON.stringify({ base_url: null, model: null, masked_key: "", configured: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const { data, error } = await api.GET("/api/settings");

    expect(error).toBeUndefined();
    expect(data?.configured).toBe(false);
  });

  it("Content-Type 大小写不敏感：`application/JSON` 也是 JSON，不该被误判成非 JSON", async () => {
    // 回归护栏：原先按字面量 `contentType.includes("json")` 判类型 —— `application/JSON`
    // 不含小写 "json"，于是一个**正常**的 JSON 响应被漏斗改判成 502「非 JSON 响应」。
    // 媒体类型的大小写不保证（代理/框架可能回 `Application/JSON`）。
    stubFetch(
      () =>
        new Response(JSON.stringify({ base_url: null, model: null, masked_key: "", configured: false }), {
          status: 200,
          headers: { "Content-Type": "Application/JSON; charset=UTF-8" },
        }),
    );

    const { data, error, response } = await api.GET("/api/settings");

    expect(response.status).toBe(200);
    expect(error).toBeUndefined();
    expect(data?.configured).toBe(false);
  });

  it("204（DELETE 投递）不被当成非 JSON 响应", async () => {
    // 204 没有正文。带 `Content-Type` 的 204 在真实服务器上并不罕见 —— 只看类型
    // 就会把一个成功的删除改判成「非 JSON 响应」错误，所以 204/304 要显式放过。
    stubFetch(
      () => new Response(null, { status: 204, headers: { "Content-Type": "text/html" } }),
    );

    const { response } = await api.DELETE("/api/applications/{application_id}", {
      params: { path: { application_id: "a1" } },
    });

    expect(response.status).toBe(204);
  });
});
