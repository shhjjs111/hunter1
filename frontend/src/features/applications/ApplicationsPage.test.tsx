import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import contract from "../../../../contracts/openapi.json";
import { ApplicationsPage } from "./ApplicationsPage";
import { STAGE_ORDER, MAX_NOTE_CHARS, type ApplicationList } from "./api";

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
  page: 1,
  page_size: 50,
  has_next: false,
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ApplicationsPage />
    </QueryClientProvider>,
  );
}

function stubFetch() {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (methodOf(input, init) === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return jsonResponse(LIST);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function deleteCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.filter(
    ([input, init]) => methodOf(input, init) === "DELETE",
  );
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
        return jsonResponse({
          ...LIST,
          items: [{ ...LIST.items[0], stage: serverStage }],
        });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ApplicationsPage />
      </QueryClientProvider>,
    );
    const select = (await screen.findByLabelText(
      /投递阶段/,
    )) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "interview" } });
    await waitFor(() => expect(select.value).toBe("interview"));

    // 别处（另一个窗口/标签页）把阶段改成「已拒」：refetch 必须反映出来 ——
    // 残留的草稿会让这里永远显示「面试」。
    serverStage = "rejected";
    await act(async () => {
      await client.refetchQueries({ queryKey: ["applications", { page: 1 }] });
    });
    expect(select.value).toBe("rejected");
  });

  it("撤草稿时下拉框不回弹到旧值（refetch 落地前保持新阶段）", async () => {
    // 乐观草稿在成功时被撤掉，而 invalidate 触发的 refetch 是异步的 —— 若撤草稿
    // 那一刻查询缓存里还是**旧** stage，下拉框会先跳回旧值（一闪）再跳到新值。
    // 这里把 refetch 挂住：撤草稿之后、refetch 落地之前，显示的必须仍是新阶段。
    let listCalls = 0;
    let releaseRefetch!: () => void;
    const refetchGate = new Promise<void>((resolve) => {
      releaseRefetch = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        if (url.includes("/stage")) {
          return jsonResponse({ application_id: "app1", stage: "interview" });
        }
        listCalls += 1;
        if (listCalls >= 2) {
          await refetchGate; // 挂住 invalidate 触发的 refetch
        }
        return jsonResponse(LIST); // 服务端仍是旧阶段 applied
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ApplicationsPage />
      </QueryClientProvider>,
    );
    const select = (await screen.findByLabelText(
      /投递阶段/,
    )) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "interview" } });

    // 等 refetch 真的发起（说明成功链已跑过），再冲干净撤草稿那一步
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
    await act(async () => {});

    // 修复前：查询缓存仍是 applied，撤草稿后 select 回落到 applied（先跳回旧值）
    expect(select.value).toBe("interview");
    expect(
      client.getQueryData<ApplicationList>(["applications", { page: 1 }])
        ?.items[0].stage,
    ).toBe("interview");

    releaseRefetch();
  });
});

describe("ApplicationsPage 分页", () => {
  it("显示真实总数", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(LIST)),
    );
    renderPage();
    expect(await screen.findByText("共 1 条")).toBeTruthy();
  });

  it("有下一页时可翻页，第二页记录仍可达", async () => {
    const pages: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const url = new URL(raw, "http://testserver");
        const page = Number(url.searchParams.get("page") ?? "1");
        pages.push(page);
        return jsonResponse({
          ...LIST,
          total: 2,
          page,
          has_next: page === 1,
          items:
            page === 1
              ? LIST.items
              : [{ ...LIST.items[0], id: "app2", title: "旧记录仍然可达" }],
        });
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /下一页/ }));

    expect(await screen.findByText("旧记录仍然可达")).toBeTruthy();
    expect(pages).toContain(2);
  });

  it("删除第二页唯一记录后自动回到第一页，不显示全空态", async () => {
    let deleted = false;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (methodOf(input, init) === "DELETE") {
          deleted = true;
          return new Response(null, { status: 204 });
        }
        const raw =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        const page = Number(
          new URL(raw, "http://testserver").searchParams.get("page") ?? "1",
        );
        if (page === 2) {
          return jsonResponse({
            ...LIST,
            total: deleted ? 1 : 2,
            page: 2,
            has_next: false,
            has_more: false,
            items: deleted
              ? []
              : [
                  {
                    ...LIST.items[0],
                    id: "app2",
                    title: "第二页唯一记录",
                  },
                ],
          });
        }
        return jsonResponse({
          ...LIST,
          total: deleted ? 1 : 2,
          has_next: !deleted,
        });
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /下一页/ }));
    await screen.findByText("第二页唯一记录");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(await screen.findByText("AI产品经理")).toBeTruthy();
    expect(screen.queryByText(/还没有投递记录/)).toBeNull();
  });
});

describe("ApplicationsPage 备注", () => {
  // 备注是一列**可编辑**的文本。此前它只显示、没有任何填写入口：改阶段的请求把
  // `item.note`（恒为 null）原样回传，于是那一列永远是「—」—— 用户看得到列名、
  // 却没有任何地方能写进去。
  function stubCapture() {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    let failPost = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        if (methodOf(input, init) === "POST") {
          const raw =
            typeof input === "string"
              ? String(init?.body ?? "")
              : await (input as Request).clone().text();
          posts.push({ url, body: JSON.parse(raw) });
          if (failPost) {
            return jsonResponse({ detail: "备注太长了" }, 422);
          }
          return jsonResponse({ application_id: "app1", stage: "applied" });
        }
        return jsonResponse(LIST);
      }),
    );
    return { posts, fail: () => (failPost = true) };
  }

  it("敲完即可保存 —— 提交当前阶段与备注", async () => {
    const { posts } = stubCapture();
    renderPage();

    const input = (await screen.findByLabelText(/的备注/)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "约了下周三面试" } });
    fireEvent.blur(input);

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain("/stage");
    expect(posts[0].body).toMatchObject({
      stage: "applied",
      note: "约了下周三面试",
    });
  });

  it("没改过就不发请求（点一下不该产生一次写入）", async () => {
    const { posts } = stubCapture();
    renderPage();

    const input = (await screen.findByLabelText(/的备注/)) as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.blur(input);

    await act(async () => {});
    expect(posts).toHaveLength(0);
  });

  it("保存失败时保留用户敲的内容（一次网络失败不该吃掉输入）", async () => {
    const { fail } = stubCapture();
    fail();
    renderPage();

    const input = (await screen.findByLabelText(/的备注/)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "别丢了我" } });
    fireEvent.blur(input);

    await screen.findByText("备注太长了");
    expect(input.value).toBe("别丢了我");
  });

  it("备注长度上限与契约一致", () => {
    const noteSchema = (
      contract as {
        components: {
          schemas: {
            StageUpdateRequest: {
              properties: { note: { anyOf?: { maxLength?: number }[] } };
            };
          };
        };
      }
    ).components.schemas.StageUpdateRequest.properties.note;
    // 契约里 `note` 是 anyOf（string | null），上限在 string 那一支上
    const limit = noteSchema.anyOf?.find(
      (branch) => branch.maxLength != null,
    )?.maxLength;
    expect(limit).toBe(MAX_NOTE_CHARS);
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

describe("ApplicationsPage 读取失败", () => {
  /**
   * 这组是「错误态与空态/加载态同屏」的回归护栏 —— 原先失败时页头永久停在
   * 「加载中…」、主体还渲染「还没有投递记录」空态，把**读失败**讲成**你没有数据**，
   * 用户于是不会去重试。此前该页面没有任何失败路径用例，所以测不出来。
   */

  function stubFailingFetch() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "服务暂时不可用" }, 503)),
    );
  }

  it("失败时不给「还没有投递记录」空态（把读失败讲成没数据）", async () => {
    stubFailingFetch();
    renderPage();
    await screen.findByText(/服务暂时不可用|加载投递记录失败/);
    expect(screen.queryByText(/还没有投递记录/)).toBeNull();
  });

  it("失败时页头不停在「加载中…」", async () => {
    stubFailingFetch();
    renderPage();
    await screen.findByText(/服务暂时不可用|加载投递记录失败/);
    expect(screen.queryByText("加载中…")).toBeNull();
  });
});

describe("ApplicationsPage 备注 / 阶段的写入互不踩踏", () => {
  // 端点 `POST /applications/{id}/stage` 是「整份状态」的写面（一次收 stage + note）。
  // 页面有两个独立的写入口（阶段下拉、备注框），若各自把**自己看到的旧值**一起提交，
  // 就会互相覆盖：先填备注再改阶段 → 备注被陈旧值抹掉 / 阶段被陈旧值打回。
  function stubPostCapture() {
    const posts: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (methodOf(input, init) === "POST") {
          const raw =
            typeof input === "string"
              ? String(init?.body ?? "")
              : await (input as Request).clone().text();
          posts.push(JSON.parse(raw));
          return jsonResponse({ application_id: "app1", stage: "interview" });
        }
        // 列表里这一行已有备注 —— 才能看出「改阶段是否把它抹掉」
        return jsonResponse({
          ...LIST,
          items: [{ ...LIST.items[0], note: "旧备注" }],
        });
      }),
    );
    return posts;
  }

  it("改阶段**不**提交备注字段（不覆盖用户刚填的备注）", async () => {
    const posts = stubPostCapture();
    renderPage();

    const select = (await screen.findByLabelText(
      /投递阶段/,
    )) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "interview" } });

    await waitFor(() => expect(posts).toHaveLength(1));
    // 省略 note 字段＝后端保留原备注；带上（哪怕是缓存旧值）就会覆盖
    expect(posts[0]).not.toHaveProperty("note");
    expect(posts[0]).toMatchObject({ stage: "interview" });
  });

  it("清空备注时提交空串（后端据此真正清掉，而不是把 null 当「保留」）", async () => {
    const posts = stubPostCapture();
    renderPage();

    const input = (await screen.findByLabelText(/的备注/)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => expect(posts).toHaveLength(1));
    // 传 null 会被后端解释为「保留原备注」→ 清空变成空操作。空串才是「清掉」。
    expect(posts[0]).toMatchObject({ note: "" });
  });
});

describe("ApplicationsPage 备注保存后不回弹（M2 回归）", () => {
  // `useSaveNote` 原先只 invalidate、不收敛缓存：组件在 onSuccess 里立刻撤掉草稿，
  // 那一刻输入框 value 回落到 `item.note`（查询缓存里的**旧**值 —— refetch 还没落地），
  // 用户看到「保存成功后输入框弹回旧内容」。这正是 `useChangeStage` 注释里描述并修掉的
  // 同类问题（阶段路径收敛了、备注路径漏了）。
  it("保存成功后输入框不回弹旧值（refetch 落地前保持新备注）", async () => {
    let listCalls = 0;
    let releaseRefetch!: () => void;
    const refetchGate = new Promise<void>((resolve) => {
      releaseRefetch = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (methodOf(input, init) === "POST") {
          return jsonResponse({ application_id: "app1", stage: "applied" });
        }
        listCalls += 1;
        if (listCalls >= 2) {
          await refetchGate; // 挂住 invalidate 触发的 refetch
        }
        return jsonResponse(LIST); // 这份响应里 note 仍是 null（服务端尚未反映）
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ApplicationsPage />
      </QueryClientProvider>,
    );
    const input = (await screen.findByLabelText(/的备注/)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "约了下周三面试" } });
    fireEvent.blur(input);

    // 等 refetch 真的发起（说明成功链已跑过），再冲干净撤草稿那一步
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
    await act(async () => {});

    // 修复前：缓存里 note 仍是 null，撤草稿后 input.value 回落到 ""（弹回旧值）
    expect(input.value).toBe("约了下周三面试");
    expect(
      client.getQueryData<ApplicationList>(["applications", { page: 1 }])
        ?.items[0].note,
    ).toBe("约了下周三面试");

    releaseRefetch();
    await act(async () => {});
  });
});
