import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("连续两次相同提示：第二条重新计时，不会沿用第一条的截止时间", async () => {
    // 第一条在 t=0 出现、4 秒后收起；第二条在 t=2.5s 出现。没有 per-toast key 时
    // Toast 的 effect 依赖里文案没变，第二条既不会重新计时也不会重新播报 ——
    // 它会在第一条的 4 秒截止点（t=4s）跟着消失。
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const request = input as Request;
        if (request.method === "POST") {
          return jsonResponse({ application_id: "a1" }, 201);
        }
        return jsonResponse(LIST_PAYLOAD);
      });
      vi.stubGlobal("fetch", fetchMock);
      renderPage();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const applyButton = screen.getByRole("button", { name: "记录投递" });

      fireEvent.click(applyButton);
      await act(async () => {
        // Toast 先挂空的 live region，下一帧才填文字（见 shared/ui 的 Toast）。
        await vi.advanceTimersByTimeAsync(16);
      });
      expect(screen.getByRole("status").textContent).toContain("已记录投递。");

      act(() => vi.advanceTimersByTime(2500));

      fireEvent.click(applyButton);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(16);
      });

      // 第一条的截止点已过 1.5 秒；第二条必须仍在。
      act(() => vi.advanceTimersByTime(3000));
      expect(screen.getByRole("status").textContent).toContain("已记录投递。");

      // 第二条自己的 4 秒到了，才允许消失。
      act(() => vi.advanceTimersByTime(1000));
      expect(screen.queryByRole("status")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("评分成功后把模型的结论摆出来，而不是只说一句「已评分」", async () => {
    // 模型算出来的三段文字（摘要 / 优势 / 差距）必须真的出现在界面上 ——
    // 否则后端落库了、界面却不读，等于「换个地方躺着」，用户仍看不到为什么是这个分。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      if (request.method === "POST") {
        return jsonResponse({
          job_id: "j1",
          score: 88,
          summary: "总体匹配",
          advantages: "有 LLM 落地经验",
          gaps: "缺大规模团队经验",
          model: "fake",
          prompt_version: "v3",
          scored_at: null,
        });
      }
      return jsonResponse(LIST_PAYLOAD);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "评分" }));

    expect(await screen.findByText("有 LLM 落地经验")).toBeTruthy();
    expect(screen.getByText("缺大规模团队经验")).toBeTruthy();
    expect(screen.getByText("总体匹配")).toBeTruthy();
    expect(screen.getByText("匹配分 88")).toBeTruthy();
    expect(await screen.findByText("已评分：88 分")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("已评分：88 分");
  });

  it("评分成功后把结果卡滚进当前视口", async () => {
    const scrollIntoView = vi.fn();
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      if (request.method === "POST") {
        return jsonResponse({
          job_id: "j1",
          score: 88,
          summary: "总体匹配",
          advantages: null,
          gaps: null,
          model: "fake",
          prompt_version: "v3",
          scored_at: null,
        });
      }
      return jsonResponse(LIST_PAYLOAD);
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "评分" }));
      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({
          behavior: "smooth",
          block: "nearest",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
    }
  });

  it("prefers-reduced-motion 下改用即时滚动", async () => {
    const scrollIntoView = vi.fn();
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: true })),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input as Request;
        if (request.method === "POST") {
          return jsonResponse({
            job_id: "j1",
            score: 88,
            summary: null,
            advantages: null,
            gaps: null,
            model: "fake",
            prompt_version: "v3",
            scored_at: null,
          });
        }
        return jsonResponse(LIST_PAYLOAD);
      }),
    );

    try {
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "评分" }));
      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({
          behavior: "auto",
          block: "nearest",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
    }
  });

  it("换关键词后清掉上一次的评分结果（卡片不与新列表错配）", async () => {
    // 评分卡不显示岗位名。翻页/换词后仍留着它，用户会以为它属于当前列表里的某一行。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      if (request.method === "POST") {
        return jsonResponse({
          job_id: "j1",
          score: 88,
          summary: "总体匹配",
          advantages: null,
          gaps: null,
          model: "fake",
          prompt_version: "v3",
          scored_at: null,
        });
      }
      return jsonResponse(LIST_PAYLOAD);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "评分" }));
    expect(await screen.findByText("匹配分 88")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("按岗位名搜索"), { target: { value: "产品" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));

    await waitFor(() => expect(screen.queryByText("匹配分 88")).toBeNull());
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
    // 503 现在透出后端写的 detail（与 409/422 同等待遇，见 shared/api/errors.ts）——
    // 原先 5xx 一刀切走 fallback，这里会是「加载岗位失败（HTTP 503）」。
    await screen.findByText(/服务暂时不可用|加载岗位失败/);
    expect(screen.queryByText("加载中…")).toBeNull();
  });
});
