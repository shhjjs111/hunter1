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
    expect((postCalls[0][0] as Request).url).toContain("/api/jobs/j1/apply");
  });
});
