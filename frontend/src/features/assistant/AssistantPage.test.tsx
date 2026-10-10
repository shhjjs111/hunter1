import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantPage } from "./AssistantPage";

const REPLY = "已为你找到 2 条岗位";
/** 只存在于会话历史里的旧消息 —— 用作「历史已加载」的锚点。 */
const EARLIER = "（更早的一轮提问）";

/**
 * 归一化两种 fetch 调用风格。
 *
 * 本项目里并存两种：`openapi-fetch` 传 **Request 对象**（url/method 在对象上），
 * 而 `shared/streaming/sse.ts` 用原生风格 `fetch(url, init)` 传**字符串 + init**。
 * 只按其中一种写 mock，另一种会在 `(input as Request).url` 上炸掉 ——
 * 这正是本测试第一版踩的坑。
 */
function reqInfo(input: RequestInfo | URL, init?: RequestInit): { url: string; method: string } {
  if (typeof input === "string") {
    return { url: input, method: (init?.method ?? "GET").toUpperCase() };
  }
  if (input instanceof URL) {
    return { url: input.href, method: (init?.method ?? "GET").toUpperCase() };
  }
  return { url: input.url, method: input.method };
}

/** 造一条 SSE 流：先一段正文，再 done（后端在 yield done 之前已落库）。 */
function sseStream(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const chunks = [
    `data: ${JSON.stringify({ type: "text", text: REPLY })}\n\n`,
    `data: ${JSON.stringify({ type: "done", conversation_id: "c1" })}\n\n`,
  ];
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i++]));
    },
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { url } = reqInfo(input, init);
      if (url.includes("/api/assistant/stream")) {
        return new Response(sseStream(), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      // 会话消息：done 时后端已落库，所以这里返回「已含本轮」的完整历史。
      // 第一条是**只存在于历史里**的旧消息，用来做「历史已加载」的锚点 ——
      // 没有它，断言「本轮恰好 1 次」会在 history 还没到、只靠 live 渲染的那一帧
      // 提前成立，测试就抓不到重复了（这正是第一版假绿的原因）。
      if (url.includes("/api/assistant/conversations/c1")) {
        return jsonResponse([
          { role: "user", content: EARLIER },
          { role: "user", content: "你好" },
          { role: "assistant", content: REPLY },
        ]);
      }
      if (url.includes("/api/assistant/conversations")) {
        return jsonResponse({ items: [{ id: "c1", title: "你好" }], total: 1, page: 1, page_size: 50, has_next: false });
      }
      return jsonResponse({});
    }),
  );
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssistantPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AssistantPage", () => {
  it("对话结束后本轮消息只显示一次（live 清空，不与会话历史叠加）", async () => {
    stubApi();
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "你好" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    // 等本轮结束：按钮从「回复中…」回到「发送」
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "发送" })).toBeDefined();
    });

    // 先等会话历史真的加载进来（否则「恰好 1 次」会在只剩 live 的那一帧提前成立）
    await screen.findByText(EARLIER);

    // 关键断言：答复恰好出现一次。
    // 不清 live 时，history（已含本轮）与本轮 live 各渲染一份 → 两遍。
    expect(screen.getAllByText(REPLY)).toHaveLength(1);
  });

  it("流式进行中禁止切换会话 —— 否则流内容会串到别的会话", async () => {
    // 用一个「卡住不结束」的流，把组件保持在 streaming 状态
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const encoder = new TextEncoder();
          return new Response(
            new ReadableStream({
              async pull(controller) {
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify({ type: "text", text: "半截" })}\n\n`),
                );
                await gate;
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    const otherConversation = await screen.findByRole("button", { name: "旧会话" });

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "在跑的时候点我试试" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await screen.findByRole("button", { name: "中止" });

    // 流式中切走会让本轮流内容追加到另一个会话上，且 done 后把 currentId 覆盖回去
    // —— 用户的选择被静默撤销。所以期间必须禁用。
    expect(otherConversation.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "新对话" }).hasAttribute("disabled")).toBe(true);

    release();
  });

  it("会话历史加载失败时，本轮消息仍然可见（历史分支不得盖掉 live）", async () => {
    // 历史请求 500、流式**进行中** —— 这正是「点开一个旧会话、继续提问」的失败形态。
    // 用卡住的流把组件保持在 streaming 状态，断言是确定的（不靠时序抢跑）。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const encoder = new TextEncoder();
          return new Response(
            new ReadableStream({
              async pull(controller) {
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify({ type: "text", text: REPLY })}\n\n`),
                );
                await gate; // done 迟迟不来 → live 消息保持在屏幕上
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations/c1")) {
          return jsonResponse({ detail: "boom" }, 500);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "旧会话" }));
    // 先确认错误提示真的出现了（否则下面的断言可能只是「那个分支没渲染」）
    await screen.findByText(/加载会话历史失败/);

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "历史坏了也要能问" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    // 关键断言：错误提示与本轮消息**同时**可见。
    // 修复前整块内容被错误分支顶掉 —— 用户刚发的问题与流式回答都不见了，
    // 而后端其实已经收到（看起来像「消息丢了」）。
    expect(await screen.findByText(REPLY)).toBeDefined();
    expect(screen.getByText("历史坏了也要能问")).toBeDefined();
    expect(screen.getByText(/加载会话历史失败/)).toBeDefined();

    release();
  });

  it("流式分片不会重建气泡（前 32 个字符内也算）", async () => {
    // 同一个气泡在流式期间必须保持同一个 DOM 节点：key 里编进内容前缀的话，
    // 内容一变 key 就变 → 每个分片都卸载重建（选区被清、动画重放）。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const chunks = [
      `data: ${JSON.stringify({ type: "text", text: "半截" })}\n\n`,
      `data: ${JSON.stringify({ type: "text", text: "继续" })}\n\n`,
      `data: ${JSON.stringify({ type: "done", conversation_id: "c1" })}\n\n`,
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const encoder = new TextEncoder();
          let index = 0;
          return new Response(
            new ReadableStream({
              async pull(controller) {
                if (index === 1) {
                  await gate; // 第二个分片前挂住，先让第一个分片落地
                }
                if (index >= chunks.length) {
                  controller.close();
                  return;
                }
                controller.enqueue(encoder.encode(chunks[index++]));
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "在吗" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    const bubble = await screen.findByText("半截");
    release();

    // 同一个节点里内容被续上（重建的话这个旧节点会从文档里脱离，文本停在「半截」）
    await waitFor(() => {
      expect(bubble.textContent).toContain("继续");
    });
    expect(screen.getAllByText(/半截继续/)).toHaveLength(1);
  });

  it("中止后说明这段回答没有保存", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const encoder = new TextEncoder();
          return new Response(
            new ReadableStream({
              async pull(controller) {
                controller.enqueue(
                  encoder.encode(
                    `data: ${JSON.stringify({ type: "text", text: "半截回答" })}\n\n`,
                  ),
                );
                await gate;
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "在吗" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await screen.findByText("半截回答");

    fireEvent.click(screen.getByRole("button", { name: "中止" }));
    release();

    // 中止的轮次后端不落库 —— 界面必须说清楚，否则切走/刷新后内容消失像丢数据
    expect(await screen.findByText(/没有保存/)).toBeDefined();
  });

  it("本轮失败时把原文还回输入框（后端不落库，否则得重打一遍）", async () => {
    // `send()` 第一件事就是 `setInput("")`。失败的轮次后端**不落库**（router 的
    // 「整轮跑完才落库」），所以不还回去的话，用户想问的那句话两头都没了。
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const body = `data: ${JSON.stringify({ type: "error", message: "上游 500" })}\n\n`;
          return new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
      }),
    );

    renderPage();
    const box = screen.getByPlaceholderText(/有什么想问的/) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "这句别丢" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    // 先确认失败路径真的走到了（否则下面的断言可能只是「还没提交」）
    expect(await screen.findByText(/上游 500/)).toBeDefined();
    await waitFor(() => {
      expect(box.value).toBe("这句别丢");
    });
  });

  it("本轮正常结束时**不**把旧问题塞回输入框", async () => {
    // 与上一条配对：正常落库的轮次把原文塞回去，用户下次会误发同一句。
    // 只钉「失败要还回去」的写法在「永远还回去」的实现下照样绿。
    stubApi();
    renderPage();
    const box = screen.getByPlaceholderText(/有什么想问的/) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "你好" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "发送" })).toBeDefined();
    });
    expect(box.value).toBe("");
  });

  it("消息区是 aria-live 的日志区，流式期间标 aria-busy", async () => {
    stubApi();
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "你好" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "发送" })).toBeDefined();
    });

    const log = await screen.findByRole("log");
    expect(log.getAttribute("aria-live")).toBe("polite");
    expect(log.getAttribute("aria-busy")).toBe("false");
  });

  it("本轮结束后历史刷新失败，回答不会凭空消失", async () => {
    // 历史一直 500，流式正常。修复前 done 里先 `setLive([])` 再 refresh：
    // 历史拉不回来 → items 变空 → 刚显示的回答（后端其实已落库）消失。
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          return new Response(sseStream(), {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (url.includes("/api/assistant/conversations/c1")) {
          return jsonResponse({ detail: "boom" }, 500);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "旧会话" }));
    await screen.findByText(/加载会话历史失败/);

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "历史坏了也要能问" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    // 等本轮真的结束（按钮从「回复中…」回到「发送」）
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "发送" })).toBeDefined();
    });

    expect(screen.getByText("历史坏了也要能问")).toBeDefined();
    expect(screen.getByText(REPLY)).toBeDefined();
  });

  it("历史在本轮流式期间到达，不会把在飞的回答抹掉", async () => {
    // 审查 P1-5：打开已有会话后**立刻**发问时，历史请求还在飞 —— 那一刻 history 是
    // []，旧实现把它当成 baseline=0；历史随后到达（这里 10 条）就判成「本轮已落库」，
    // 于是用户刚发的问题和**正在流式输出**的回答一起从屏幕上消失，
    // 而输入框只以 streaming 为禁用条件、historyLoading 不拦发问，窗口可达。
    let releaseHistory!: () => void;
    const historyGate = new Promise<void>((resolve) => {
      releaseHistory = resolve;
    });

    let push!: (chunk: string) => void;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(encoder.encode(chunk));
      },
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          return new Response(stream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (url.includes("/api/assistant/conversations/c1")) {
          await historyGate; // 卡住历史请求，模拟「历史比发问晚到」
          return jsonResponse([
            { role: "user", content: EARLIER },
            ...Array.from({ length: 9 }, (_, index) => ({
              role: "assistant",
              content: `旧回答 ${index}`,
            })),
          ]);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "旧会话" }));
    await screen.findByText(/加载会话历史/); // 历史仍在飞

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "在飞的提问" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    // 流开始输出（还没 done）
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "回复中…" })).toBeDefined();
    });
    act(() => {
      push(`data: ${JSON.stringify({ type: "text", text: REPLY })}\n\n`);
    });
    await screen.findByText(REPLY);

    // 历史这时才到达：条数从 0 变成 10 —— 旧判据（length > baseline）就此成立
    act(() => {
      releaseHistory();
    });
    await screen.findByText(EARLIER);

    // 关键断言：在飞的回答与刚发的问题都还在（旧实现在这里双双消失）
    expect(screen.getByText("在飞的提问")).toBeDefined();
    expect(screen.getByText(REPLY)).toBeDefined();
    expect(screen.getByRole("button", { name: "回复中…" })).toBeDefined();
  });

  it("历史到达时已含同文本的旧提问，在飞的回答也不会被抹掉", async () => {
    // 这条专门钉 `turnSettled` 那道闸：会话里之前问过同一句话时，历史到达的瞬间
    // 「同文本用户消息数」就已经比发问时多了一条 —— 只按内容判断会在流还没结束时
    // 就认定「本轮已落库」，把在飞的回答抹掉。必须等本轮真正结束（done）再判。
    let releaseHistory!: () => void;
    const historyGate = new Promise<void>((resolve) => {
      releaseHistory = resolve;
    });

    let push!: (chunk: string) => void;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(encoder.encode(chunk));
      },
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          return new Response(stream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (url.includes("/api/assistant/conversations/c1")) {
          await historyGate;
          // 关键：历史里已经有一条**同文本**的旧提问
          return jsonResponse([
            { role: "user", content: "同一句提问" },
            { role: "assistant", content: "上一次的回答" },
          ]);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [{ id: "c1", title: "旧会话" }], total: 1, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "旧会话" }));
    await screen.findByText(/加载会话历史/);

    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "同一句提问" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "回复中…" })).toBeDefined();
    });
    act(() => {
      push(`data: ${JSON.stringify({ type: "text", text: "这一轮的回答" })}\n\n`);
    });
    await screen.findByText("这一轮的回答");

    act(() => {
      releaseHistory();
    });
    await screen.findByText("上一次的回答");

    // 本轮还没结束：在飞内容必须留着（两道提问都可见 —— 一条来自历史、一条是本轮）
    expect(screen.getAllByText("同一句提问")).toHaveLength(2);
    expect(screen.getByText("这一轮的回答")).toBeDefined();
  });
});

describe("AssistantPage 流安静地结束（既没 done 也没 error）", () => {
  /**
   * 回归护栏：上游/代理把连接正常收尾却**从不发 done** 时，半截回答会被当成本轮
   * 正常结果 —— 用户以为助手说完了，而后端其实没落库（落库发生在 yield done 之前）。
   * 这就是「静默截断」：半截内容 + 无任何提示。
   */
  it("给出「连接中断」提示，而不是静默把半截回答当结果", async () => {
    const encoder = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : (input as Request).url;
        if (url.includes("/api/assistant/stream")) {
          return new Response(
            new ReadableStream({
              pull(controller) {
                // 只发一段文本，然后**直接关闭** —— 没有 done、没有 error。
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify({ type: "text", text: "半截回答" })}\n\n`),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), {
      target: { value: "你还在吗" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    expect(await screen.findByText(/连接中断/)).toBeTruthy();
    // 半截回答仍然显示（用户要能看到它），但必须伴随提示。
    expect(screen.getByText(/半截回答/)).toBeTruthy();
  });
});

describe("AssistantPage SSE 块解析失败", () => {
  /**
   * 回归护栏：中途有 SSE 块解析不出来（内容丢了）时，屏幕上的回答缺一段，
   * 却会被当成本轮完整结果呈现 —— 与上面「不许静默截断」的规矩自相矛盾。
   * 必须给出提示，让用户知道这段回答可能不完整。
   */
  it("有内容未能解析时给出提示，而不是把缺段的回答当完整结果", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const encoder = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const chunks = [
            `data: ${JSON.stringify({ type: "text", text: REPLY })}\n\n`,
            "data: {坏掉的\n\n", // 这一段没能解析 —— 回答因此缺了一段
            `data: ${JSON.stringify({ type: "done", conversation_id: "c1" })}\n\n`,
          ];
          let i = 0;
          return new Response(
            new ReadableStream({
              pull(controller) {
                if (i >= chunks.length) {
                  controller.close();
                  return;
                }
                controller.enqueue(encoder.encode(chunks[i++]));
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations/c1")) {
          return jsonResponse([]);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "你好" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    expect(await screen.findByText(/有内容未能解析/)).toBeTruthy();
    // 已经收到的部分照常显示（用户要能看见它），但必须伴随提示。
    expect(screen.getByText(REPLY)).toBeTruthy();
    warn.mockRestore();
  });
});

describe("AssistantPage 中止提示的判据不依赖渲染时序", () => {
  /**
   * 回归护栏：中止时「屏幕上有没有半截回答」必须由「本轮是否收到过助手正文」决定，
   * **不是**由「React 有没有已经把它提交到 DOM」决定。
   *
   * 原先读的是 `live` 的 effect 镜像，而镜像要等提交之后才更新。于是当第一个分片与
   * 中止落在同一个任务内（分片已处理、还没提交）时，镜像读到的还是空数组 ——
   * 屏幕随后显示出半截回答，却没有「没有保存」的提示。实测：让出一个宏任务后再中止，
   * 行为就是对的，差别只在提交时序。这个窗口小于一个宏任务、人手点不到，
   * 但正确性不该依赖调度时序（判据改成了 appendAssistantText 里的同步置位）。
   */
  it("分片已处理但尚未提交就中止，仍要说清「没有保存」", async () => {
    let push!: (chunk: string) => void;
    let close!: () => void;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(encoder.encode(chunk));
        close = () => controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          return new Response(stream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "在吗" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await screen.findByRole("button", { name: "中止" });

    // 推一个分片后只抽干微任务：分片已被处理，但 React 还没提交它。
    push(`data: ${JSON.stringify({ type: "text", text: "半截回答" })}\n\n`);
    for (let i = 0; i < 20; i += 1) {
      await Promise.resolve();
    }

    fireEvent.click(screen.getByRole("button", { name: "中止" }));
    // 测试里的假流不理会 abort 信号：手动收尾，让 send() 走到判断那一步。
    close();
    await act(async () => {});

    expect(screen.getByText(/没有保存/)).toBeDefined();
  });
});

describe("AssistantPage 会话列表分页", () => {
  /**
   * 回归护栏：会话列表是**分页**的（后端 `page_size` 上限 50），第 51 个起的会话
   * 必须翻页够得着。
   *
   * 之前这里只有一句「还有更多」而没有翻页入口 —— 那些会话不是「不可见」，而是
   * **无法触达**：数据在库里，界面没有任何办法把它取出来。
   */
  function stubPages(): string[] {
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/conversations")) {
          const matched = url.match(/[?&]page=(\d+)/);
          const page = matched ? matched[1] : "1";
          requested.push(page);
          return page === "2"
            ? jsonResponse({
                items: [{ id: "old", title: "更旧的会话" }],
                total: 60,
                page: 2,
                page_size: 50,
                has_next: false,
              })
            : jsonResponse({
                items: [{ id: "c1", title: "最近的会话" }],
                total: 60,
                page: 1,
                page_size: 50,
                has_next: true,
              });
        }
        return jsonResponse({});
      }),
    );
    return requested;
  }

  it("有下一页时给分页控件，翻页真的会去取第 2 页", async () => {
    const requested = stubPages();

    renderPage();
    expect(await screen.findByText("最近的会话")).toBeDefined();
    expect(await screen.findByText("共 60 个会话")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));

    // 第 2 页的内容真的取回来了（不只是 UI 变了个数字）
    expect(await screen.findByText("更旧的会话")).toBeDefined();
    expect(requested).toContain("2");
    expect(screen.getByText("第 2 页")).toBeDefined();
  });

  it("没有下一页时不给分页控件（不制造假警报）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({
            items: [{ id: "c1", title: "唯一的会话" }],
            total: 1,
            page: 1,
            page_size: 50,
            has_next: false,
          });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    await screen.findByText("唯一的会话");
    expect(screen.queryByRole("navigation", { name: "会话分页" })).toBeNull();
    expect(screen.queryByText(/共 /)).toBeNull();
  });
});

describe("AssistantPage 提示优先级（截断比丢块严重）", () => {
  /**
   * 回归护栏：一轮里**同时**发生「done 说截断」与「有块没解析出来」时，两条提示
   * 不能互相覆盖 —— 截断说明整段回答本身就不完整（且已按完整结果落库），比「屏幕
   * 上少了一段」更严重，必须胜出。原先丢块分支无条件 setNotice，会把 done 刚设的
   * 截断提示盖掉。
   */
  it("done 报了截断时，丢块提示不覆盖截断提示", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const encoder = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          const chunks = [
            `data: ${JSON.stringify({ type: "text", text: REPLY })}\n\n`,
            "data: {坏掉的\n\n", // 这一段没能解析 —— 回答因此缺了一段
            `data: ${JSON.stringify({ type: "done", conversation_id: "c1", truncated: true })}\n\n`,
          ];
          let i = 0;
          return new Response(
            new ReadableStream({
              pull(controller) {
                if (i >= chunks.length) {
                  controller.close();
                  return;
                }
                controller.enqueue(encoder.encode(chunks[i++]));
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations/")) {
          return jsonResponse([]);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "你好" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    expect(await screen.findByText(/截断/)).toBeTruthy();
    expect(screen.queryByText(/有内容未能解析/)).toBeNull();
    warn.mockRestore();
  });
});

describe("AssistantPage 卸载后不再更新", () => {
  /**
   * 回归护栏：卸载时只 `abort()` 不够 —— abort 停的是网络，流的回调与 `send()` 的
   * 异步续点仍会继续跑，对已卸载的组件 setState，并在 `done` 里触发一次 assistant
   * 重取。React 18+ 会静默忽略这些 setState（本仓实测：不产生任何警告），所以断言
   * 取那个**可观察**的副作用 —— 卸载后到达的 done 不该再触发 `invalidateQueries`。
   */
  it("卸载后到达的 done 不再触发重取（abort 只停网络）", async () => {
    let push!: (chunk: string) => void;
    let close!: () => void;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(encoder.encode(chunk));
        close = () => controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          return new Response(stream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    // 只观察不改行为：refresh() 是这一轮里唯一会调 invalidateQueries 的地方。
    const spy = vi.spyOn(client, "invalidateQueries");
    const view = render(
      <QueryClientProvider client={client}>
        <AssistantPage />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "在吗" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
    await screen.findByRole("button", { name: "中止" });

    view.unmount(); // 卸载：只 abort；假流不理会 abort，回调仍会继续跑

    // 卸载之后到达的 done —— 修复前会 setCurrentId / setPage 并 refresh()
    await act(async () => {
      push(`data: ${JSON.stringify({ type: "done", conversation_id: "c1" })}\n\n`);
      close();
    });

    expect(spy).not.toHaveBeenCalled();
  });
});

describe("AssistantPage 会话列表的加载/错误态", () => {
  /**
   * 回归护栏：会话列表原先没有加载/错误状态 —— 读失败或正在读时侧栏只是一个空
   * 列表，看起来就是「一条会话都没有」，用户不会想到去重试（对齐 ApplicationsPage
   * 对「失败 ≠ 空」的处理）。
   */
  it("会话列表加载中给出加载态（不把「读着」当成「没有会话」）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})), // 永不返回
    );
    renderPage();
    expect(await screen.findByText("加载会话列表…")).toBeDefined();
  });

  it("会话列表读取失败时给出错误提示（不把读失败当成「没有会话」）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ detail: "会话列表炸了" }, 503);
        }
        return jsonResponse({});
      }),
    );
    renderPage();
    expect(await screen.findByText(/加载会话失败/)).toBeDefined();
  });
});

describe("AssistantPage 大量分片拼接", () => {
  /**
   * 流式正文用「数组收集 + join 拼」而不是每片 `content + chunk`（后者 O(n²)）。
   * 这条钉的是拼接的**正确性**：几百片既不能丢，也不能被拆成多个气泡。
   */
  it("几百个分片拼成一整段，不丢片", async () => {
    const segments = Array.from({ length: 200 }, (_, i) => `p${i}-`);
    const encoder = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const { url } = reqInfo(input, init);
        if (url.includes("/api/assistant/stream")) {
          let i = 0;
          return new Response(
            new ReadableStream({
              pull(controller) {
                if (i < segments.length) {
                  controller.enqueue(
                    encoder.encode(
                      `data: ${JSON.stringify({ type: "text", text: segments[i++] })}\n\n`,
                    ),
                  );
                  return;
                }
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify({ type: "done", conversation_id: "c1" })}\n\n`),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.includes("/api/assistant/conversations/")) {
          return jsonResponse([]);
        }
        if (url.includes("/api/assistant/conversations")) {
          return jsonResponse({ items: [], total: 0, page: 1, page_size: 50, has_next: false });
        }
        return jsonResponse({});
      }),
    );

    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/有什么想问的/), { target: { value: "在吗" } });
    fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);

    expect(await screen.findByText(segments.join(""), {}, { timeout: 5000 })).toBeTruthy();
  });
});
