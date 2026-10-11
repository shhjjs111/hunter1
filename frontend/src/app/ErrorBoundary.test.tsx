import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

/**
 * 错误边界此前**零测试**：它是「单个 feature 崩了不打成白屏」的唯一防线，
 * 而三条行为（透传 children / 显示可复制的原因 / 重试复位）都没被钉住。
 */
function Boom(): never {
  throw new Error("渲染炸了");
}

describe("ErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("正常时透传 children（边界不该改变正常渲染）", () => {
    render(
      <ErrorBoundary>
        <p>正常内容</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("正常内容")).toBeDefined();
  });

  it("子组件抛错时显示错误信息，而不是白屏", () => {
    // 本地工具没有远程日志：错误消息必须**显示在页面上**，用户才能直接复制给维护者
    // （见组件注释）。白屏或只写 console 都等于把线索藏起来。
    // React 会把边界捕获的错误打到 console.error —— 这里静音，只断言界面行为。
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText("这个页面出错了")).toBeDefined();
    expect(screen.getByText("渲染炸了")).toBeDefined();
  });

  it("点「重试」后复位并重新渲染 children", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) {
        throw new Error("第一次失败");
      }
      return <p>恢复了</p>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByText("这个页面出错了")).toBeDefined();

    // 让下一次渲染不再抛 —— 重试按钮的语义正是「再渲染一次」
    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(screen.getByText("恢复了")).toBeDefined();
  });
});
