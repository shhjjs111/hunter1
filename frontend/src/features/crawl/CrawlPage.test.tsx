import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CrawlPage } from "./CrawlPage";

const IDLE = { running: false, sites: [], total_fetched: 0, error: null };
const RUNNING = { ...IDLE, running: true };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  // 与 App.tsx 的真实配置一致 —— 特别是 staleTime。
  // 不带上它，测试会因为「查询已过期而自然重取」而变绿，抓不到这个缺陷。
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CrawlPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CrawlPage", () => {
  it("点击「开始抓取」后立即重取状态 —— 否则界面最长 30 秒纹丝不动", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input as Request;
        calls.push(`${request.method} ${new URL(request.url).pathname}`);
        if (request.method === "POST") {
          return jsonResponse({ started: true });
        }
        return jsonResponse(RUNNING);
      }),
    );

    renderPage();
    await screen.findByRole("button", { name: /开始抓取/ });
    const before = calls.filter((c) => c.startsWith("GET")).length;

    fireEvent.click(screen.getByRole("button", { name: /开始抓取/ }));

    // 启动抓取后必须重新拉状态：否则缓存里 running 仍是 false，
    // 而 status 的 refetchInterval 只在 running 时轮询 → 进度界面不动。
    await waitFor(() => {
      expect(calls.filter((c) => c.startsWith("GET")).length).toBeGreaterThan(before);
    });
  });

  it("抓取进行中时按钮禁用并改文案", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(RUNNING)),
    );
    renderPage();
    const button = await screen.findByRole("button", { name: /正在抓取/ });
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});
