import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import contract from "../../../../contracts/openapi.json";
import { ApplicationsPage } from "./ApplicationsPage";
import { STAGE_ORDER } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const LIST = {
  items: [
    {
      id: "app1",
      job_id: "j1",
      company: "字节跳动",
      title: "AI产品经理",
      stage: "applied",
      applied_at: "2026-10-05T00:00:00Z",
      updated_at: "2026-10-05T00:00:00Z",
      note: null,
    },
  ],
};

/** openapi-fetch 传的是 Request 对象（method 在对象上），SSE 才是 url+init。 */
function methodOf(input: RequestInfo | URL, init?: RequestInit): string {
  if (typeof input === "object" && input !== null && "method" in input) {
    return (input as Request).method;
  }
  return init?.method ?? "GET";
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApplicationsPage />
    </QueryClientProvider>,
  );
}

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (methodOf(input, init) === "DELETE") {
      return new Response(null, { status: 204 });
    }
    return jsonResponse(LIST);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function deleteCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.filter(([input, init]) => methodOf(input, init) === "DELETE");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ApplicationsPage 删除确认", () => {
  it("确认框取消时**不**发删除请求（删除不可撤销，误点不该丢记录）", async () => {
    const fetchMock = stubFetch();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteCalls(fetchMock)).toHaveLength(0);
  });

  it("确认后才发删除请求", async () => {
    const fetchMock = stubFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(deleteCalls(fetchMock)).toHaveLength(1);
    });
  });
});

describe("前端阶段枚举与契约一致", () => {
  // STAGE_ORDER 是**手抄**的枚举取值表，会与后端漂移。拿契约快照逐条比对：
  // 契约里 stage 现在是真 enum（后端用 ApplicationStage 换来），pydantic 把
  // 它提成了独立的 `ApplicationStage` schema（`$ref` 指向），增删阶段而忘了
  // 同步前端时这里会红 —— 不必靠人工记得同步。
  it("STAGE_ORDER 与契约的 ApplicationStage enum 取值完全一致", () => {
    const enumValues = (
      contract as {
        components: { schemas: { ApplicationStage: { enum: string[] } } };
      }
    ).components.schemas.ApplicationStage.enum;

    expect([...STAGE_ORDER].sort()).toEqual([...enumValues].sort());
    expect(new Set(STAGE_ORDER).size).toBe(STAGE_ORDER.length); // 无重复
  });
});
