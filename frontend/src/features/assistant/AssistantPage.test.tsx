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
        return jsonResponse([{ id: "c1", title: "你好" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
        return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
          return jsonResponse([{ id: "c1", title: "旧会话" }]);
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
