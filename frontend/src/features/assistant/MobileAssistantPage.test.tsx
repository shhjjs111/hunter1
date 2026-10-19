import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeChrome, renderWithChrome, stubMedia } from "../../test-utils";
import { MobileAssistantPage } from "./MobileAssistantPage";

const state = vi.hoisted(() => ({
  chat: {} as Record<string, unknown>,
}));

vi.mock("./useAssistantChat", () => ({
  useAssistantChat: () => state.chat,
}));

function makeChat(overrides: Record<string, unknown> = {}) {
  return {
    currentId: null,
    page: 1,
    setPage: vi.fn(),
    conversations: {
      isLoading: false,
      isError: false,
      error: null,
      data: {
        items: [{ id: "c1", title: "旧会话", updated_at: "2026-10-18T00:00:00Z" }],
        total: 1,
        page: 1,
        page_size: 50,
        has_next: false,
      },
    },
    messages: { error: null },
    input: "",
    setInput: vi.fn(),
    streaming: false,
    error: null,
    notice: null,
    items: [],
    historyLoading: false,
    historyError: false,
    send: vi.fn().mockResolvedValue(undefined),
    abort: vi.fn(),
    newConversation: vi.fn(),
    openConversation: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  stubMedia({ reduced: true });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  state.chat = makeChat();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileAssistantPage", () => {
  it("空会话展示只读说明、问候与建议", async () => {
    const { chrome } = renderWithChrome(<MobileAssistantPage />);
    expect(screen.getByText(/只读：它查岗位与投递/)).toBeTruthy();
    expect(screen.getByText(/你好，我能查你的岗位库/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "哪些岗位还没评分？" }));
    expect((state.chat.send as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith("哪些岗位还没评分？");
    expect(chrome.setOverlayOpen).toHaveBeenCalledWith(false);
  });

  it("输入并提交；流式期间按钮变为中止", () => {
    state.chat = makeChat({ input: "帮我准备面试" });
    const { rerender, chrome } = renderWithChrome(<MobileAssistantPage />);
    const input = screen.getByRole("textbox", { name: "输入要问助手的问题" });
    fireEvent.change(input, { target: { value: "帮我准备二面" } });
    expect((state.chat.setInput as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith("帮我准备二面");
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect((state.chat.send as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
    fireEvent.blur(input);
    expect(chrome.setKeyboardOpen).toHaveBeenCalledWith(false);

    state.chat = makeChat({ streaming: true, input: "回答中" });
    rerender(<MobileAssistantPage />);
    fireEvent.click(screen.getByRole("button", { name: "中止回答" }));
    expect((state.chat.abort as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
  });

  it("消息、工具状态与流式光标", () => {
    state.chat = makeChat({
      streaming: true,
      items: [
        { role: "user", content: "问题" },
        { role: "tool", content: "查询岗位库 · 找到 6 条" },
        { role: "assistant", content: "回答" },
      ],
    });
    renderWithChrome(<MobileAssistantPage />);
    expect(screen.getByText("问题")).toBeTruthy();
    expect(screen.getByText("✦ 查询岗位库 · 找到 6 条")).toBeTruthy();
    expect(screen.getByText("回答").querySelector(".mobile-caret")).toBeTruthy();
  });

  it("历史加载失败与截断提示", () => {
    state.chat = makeChat({
      historyError: true,
      messages: { error: new Error("历史读不到") },
      notice: "回答达到轮次上限被截断，内容可能不完整。",
      error: null,
    });
    renderWithChrome(<MobileAssistantPage />);
    expect(screen.getByText(/加载会话历史失败：历史读不到/)).toBeTruthy();
    expect(screen.getByText("回答达到轮次上限被截断，内容可能不完整。")).toBeTruthy();
  });

  it("会话抽屉：列表、新建、打开与分页", async () => {
    state.chat = makeChat({
      page: 2,
      conversations: {
        isLoading: false,
        isError: false,
        error: null,
        data: {
          items: [{ id: "c1", title: "旧会话", updated_at: "2026-10-18T00:00:00Z" }],
          total: 60,
          page: 2,
          page_size: 50,
          has_next: true,
        },
      },
    });
    const chrome = makeChrome({ drawerOpen: true });
    renderWithChrome(<MobileAssistantPage />, chrome);

    const drawer = screen.getByRole("dialog", { name: "会话列表" });
    await waitFor(() => expect(document.activeElement).toBe(drawer));
    fireEvent.click(within(drawer).getByRole("button", { name: "新对话" }));
    expect((state.chat.newConversation as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
    expect(chrome.setDrawerOpen).toHaveBeenCalledWith(false);

    fireEvent.click(within(drawer).getByRole("button", { name: /旧会话/ }));
    expect((state.chat.openConversation as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith("c1");

    fireEvent.click(within(drawer).getByRole("button", { name: "上一页" }));
    expect((state.chat.setPage as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(1);
    fireEvent.click(within(drawer).getByRole("button", { name: "下一页" }));
    expect((state.chat.setPage as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(3);
  });

  it("Esc 关闭抽屉，输入聚焦会通知外壳隐藏 Dock", () => {
    const chrome = makeChrome({ drawerOpen: true });
    renderWithChrome(<MobileAssistantPage />, chrome);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(chrome.setDrawerOpen).toHaveBeenCalledWith(false);

    const input = screen.getByRole("textbox", { name: "输入要问助手的问题" });
    fireEvent.focus(input);
    expect(chrome.setKeyboardOpen).toHaveBeenCalledWith(true);
  });

  it("会话列表为空时给出开始新对话的提示", async () => {
    state.chat = makeChat({
      conversations: {
        isLoading: false,
        isError: false,
        error: null,
        data: { items: [], total: 0, page: 1, page_size: 50, has_next: false },
      },
    });
    const chrome = makeChrome({ drawerOpen: true });
    renderWithChrome(<MobileAssistantPage />, chrome);
    expect(await screen.findByText(/还没有会话/)).toBeTruthy();
  });
});
