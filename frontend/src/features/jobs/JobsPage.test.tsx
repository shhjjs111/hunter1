import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobsPage } from "./JobsPage";

const LIST_PAYLOAD = {
  items: [
    {
      id: "j1",
      title: "AI产品经理",
      company: "字节跳动",
      city: "北京",
      match_score: 88,
      source: "实习僧",
      capture_status: "complete",
      detail_url: "https://x/1",
      last_seen_at: null,
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
  has_next: false,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <JobsPage />
    </QueryClientProvider>,
  );
}

describe("JobsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("加载并渲染岗位列表（含总数）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(LIST_PAYLOAD)),
    );
    renderPage();
    expect(await screen.findByText("AI产品经理")).toBeTruthy();
    expect(screen.getByText("共 1 条")).toBeTruthy();
  });

  it("加载失败时显示错误信息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "boom" }, 500)),
    );
    renderPage();
    expect(await screen.findByText(/加载岗位失败/)).toBeTruthy();
  });

  it("后端连不上时给出可读提示，而不是英文 Failed to fetch", async () => {
    // 这是审查里那条「用户看到的是英文 Failed to fetch」的验收：fetch 直接 reject
    // （后端没起、连接被拒）时，界面必须说清怎么办。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderPage();
    const message = await screen.findByText(/无法连接后端/);
    expect(message.textContent).toContain("请确认 Hunter1 服务还在运行");
    expect(screen.queryByText(/^Failed to fetch$/)).toBeNull();
  });

  it("记录投递：POST 后给出成功提示", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      // openapi-fetch 以 Request 对象调用 fetch（method 在对象上，无第二参数）
      const request = input as Request;
      if (request.method === "POST") {
        return jsonResponse({ application_id: "a1" }, 201);
      }
      return jsonResponse(LIST_PAYLOAD);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "记录投递" }));
    expect(await screen.findByText("已记录投递。")).toBeTruthy();

    const postCalls = fetchMock.mock.calls.filter(
      ([input]) => (input as Request).method === "POST",
    );
    expect(postCalls).toHaveLength(1);
    // 投递入口已从 jobs 迁到 applications（投递记录本体归 applications 切片）
    expect((postCalls[0][0] as Request).url).toContain("/api/applications");
  });
});

describe("JobsPage 读取失败", () => {
  /** 回归护栏：读取失败时页头不能停在「加载中…」—— 它永远不会变，会与错误提示长期矛盾。 */
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("失败时页头不停在「加载中…」，且给出可读原因", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "服务暂时不可用" }, 503)),
    );
    renderPage();
    await screen.findByText(/加载岗位失败/);
    expect(screen.queryByText("加载中…")).toBeNull();
  });
});
