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

  it("首屏不给空白卡片（那与「一个站点都没有」长得一样）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})), // 状态请求永不返回
    );
    renderPage();
    expect(await screen.findByText("正在读取抓取进度…")).toBeDefined();
  });

  it("「上一轮还在跑」的提示只在确实还在跑时出现", async () => {
    // 后端拒绝（started:false）= 它那边有一轮在跑；此时刷新出来的状态会说
    // running:true，提示才该出现。
    let posted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if ((input as Request).method === "POST") {
          posted = true;
          return jsonResponse({ started: false });
        }
        return jsonResponse(posted ? RUNNING : IDLE);
      }),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /开始抓取/ }));
    expect(await screen.findByText(/上一轮还在跑/)).toBeDefined();
  });

  it("上一轮已经跑完时提示不滞留", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if ((input as Request)?.method === "POST") {
          return jsonResponse({ started: false });
        }
        return jsonResponse(IDLE);
      }),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /开始抓取/ }));

    // 等拒绝结果真的落地（按钮重新可点）
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /开始抓取/ }).hasAttribute("disabled")).toBe(false),
    );
    expect(screen.queryByText(/上一轮还在跑/)).toBeNull();
  });

  it("一轮跑完（running: true → false）时失效岗位库", async () => {
    // 不失效 ["jobs"] 的话，岗位库的 staleTime 是 30 秒 —— 用户抓完立刻回去看，
    // 还是本轮之前的列表，看起来像「抓了但没进来」。
    let statusCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        statusCalls += 1;
        return jsonResponse(statusCalls <= 1 ? RUNNING : IDLE);
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    // 只观察不改行为：`vi.spyOn` 默认调用原实现（透传），顺带把参数记下来
    const spy = vi.spyOn(client, "invalidateQueries");
    render(
      <QueryClientProvider client={client}>
        <CrawlPage />
      </QueryClientProvider>,
    );

    await screen.findByRole("button", { name: /正在抓取/ });
    // 状态轮询（1 秒一次）拿到 idle 后应失效 ["jobs"]
    await waitFor(
      () => {
        const keys = spy.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
        expect(keys).toContain('["jobs"]');
      },
      { timeout: 5000 },
    );
  });
});
