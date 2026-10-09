import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("CrawlPage 读取失败", () => {
  /**
   * 回归护栏：状态读取失败时，原先会**同屏**显示「错误提示」与一张永久挂着的
   * 「正在读取抓取进度…」卡片（轮询在失败期间一直重试，snapshot 恒为 undefined），
   * 两条信息自相矛盾。此前该页面没有 status 失败用例，所以测不出来。
   */
  it("失败时不再显示「正在读取抓取进度…」卡片", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "后端暂时不可用" }, 503)),
    );
    renderPage();
    await screen.findByText(/后端暂时不可用|读取抓取进度失败/);
    expect(screen.queryByText("正在读取抓取进度…")).toBeNull();
  });

  it("失败时页头不停在「正在读取状态…」", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "后端暂时不可用" }, 503)),
    );
    renderPage();
    await screen.findByText(/后端暂时不可用|读取抓取进度失败/);
    expect(screen.queryByText("正在读取状态…")).toBeNull();
  });
});

describe("CrawlPage 后端不可达时的轮询退避", () => {
  /**
   * 回归护栏：后端倒下后，一个开着的页面会一直重试 —— 没有退避时是**每秒**一次，
   * 无限期地给已经起不来的后端施压。这里用假时钟量「10 秒内到底打了几次」：
   * 固定 1 秒间隔会产生约 10 次，指数退避（1→2→4→8…）应只有 2 次。
   */
  it("退避后不再每秒重试（10 秒内的请求次数远少于固定间隔）", async () => {
    vi.useFakeTimers();
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return jsonResponse({ detail: "后端暂时不可用" }, 503);
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    try {
      render(
        <QueryClientProvider client={client}>
          <CrawlPage />
        </QueryClientProvider>,
      );
      // 挂载即发首次请求
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(calls).toBeGreaterThan(0);
      const afterFirst = calls;

      // 之后 10 秒：固定 1 秒 = 约 10 次；退避应 ≤ 3 次
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(calls - afterFirst).toBeLessThanOrEqual(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("上一次成功是空闲态、之后读取失败 —— 轮询不会停死（陈旧快照不是结论）", async () => {
    vi.useFakeTimers();
    let calls = 0;
    let failing = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return failing ? jsonResponse({ detail: "后端暂时不可用" }, 503) : jsonResponse(IDLE);
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    try {
      render(
        <QueryClientProvider client={client}>
          <CrawlPage />
        </QueryClientProvider>,
      );
      // 首次成功：后端说空闲 —— 此时按设计**停止**轮询
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(calls).toBe(1);

      // 后端变得不可达，并触发一次后台重取（真实里来自窗口重新聚焦 / staleTime）
      failing = true;
      await act(async () => {
        await client.refetchQueries({ queryKey: ["crawl", "status"] });
      });
      const afterFailure = calls;
      expect(afterFailure).toBeGreaterThan(1);

      // 修复前这一步失败：react-query 保留了上一次的 `{running:false}`，于是
      // crawlPollInterval 返回 false，此后**一次都不会再重试** —— 后端恢复后界面
      // 永远停在旧快照，而用户没有任何刷新入口。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(calls).toBeGreaterThan(afterFailure);
    } finally {
      vi.useRealTimers();
    }
  });
});
