import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  total: 1,
  has_more: false,
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

describe("ApplicationsPage 阶段乐观值", () => {
  // 这里刻意**不**测「第一次请求失败会不会把第二次的乐观值抹掉」：@tanstack/react-query
  // v5 的 `MutationObserver.mutate()` 会先 `#currentMutation?.removeObserver(this)`，
  // 同一行连改两次时**前一次的回调根本不会被调用**（实测：在 onError 里打断点不命中）。
  // 也就是说那条路径在当前版本不可达 —— 写一条「无论修没修都通过」的用例只会给出假信心。
  // 真正可达、也真正在页面上的问题是下面这条：成功后的草稿必须交还给查询数据。
  it("成功后草稿交还给服务端（后续 refetch 的结果不被永久压住）", async () => {
    // 乐观草稿若不在成功时清掉，就会**永久**压住查询数据：别处（另一个窗口、
    // 或后端归一化后的值）改出来的新阶段永远显示不出来 —— 用户看到的是自己那次
    // 点击留下的旧值，刷新页面才恢复。
    let serverStage = "applied";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        if (url.includes("/stage")) {
          serverStage = "interview";
          return jsonResponse({ application_id: "app1", stage: serverStage });
        }
        return jsonResponse({ ...LIST, items: [{ ...LIST.items[0], stage: serverStage }] });
      }),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ApplicationsPage />
      </QueryClientProvider>,
    );
    const select = (await screen.findByLabelText(/投递阶段/)) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "interview" } });
    await waitFor(() => expect(select.value).toBe("interview"));

    // 别处（另一个窗口/标签页）把阶段改成「已拒」：refetch 必须反映出来 ——
    // 残留的草稿会让这里永远显示「面试」。
    serverStage = "rejected";
    await act(async () => {
      await client.refetchQueries({ queryKey: ["applications"] });
    });
    expect(select.value).toBe("rejected");
  });
});

describe("ApplicationsPage 截断信号", () => {
  // 列表有固定上限（后端 LIST_LIMIT=200）。响应里的 total/has_more 后端特意算了
  // 「让截断不再静默」—— 页面必须用它，而不是拿 items.length 冒充「共 N 条」。
  it("列表被截断时显示真实总数与「只显示最近 N 条」", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ...LIST, total: 350, has_more: true })),
    );

    renderPage();

    expect(await screen.findByText("共 350 条，只显示最近 1 条")).toBeTruthy();
  });

  it("未截断时只显示总数", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(LIST)));

    renderPage();

    expect(await screen.findByText("共 1 条")).toBeTruthy();
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
