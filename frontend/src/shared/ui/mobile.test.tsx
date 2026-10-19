import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionSheet, ConfirmDialog, MobileIcon, Sheet, type MobileIconName } from "./mobile";

const ICONS: MobileIconName[] = [
  "search",
  "menu",
  "plus",
  "chevron",
  "check",
  "eye",
  "eyeOff",
  "send",
  "stop",
  "close",
  "external",
  "sun",
  "moon",
];

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileIcon", () => {
  it("每个图标名都能渲染出 svg（图标是装饰，读屏忽略）", () => {
    for (const name of ICONS) {
      const { container, unmount } = render(<MobileIcon name={name} />);
      const svg = container.querySelector("svg");
      expect(svg?.getAttribute("aria-hidden")).toBe("true");
      unmount();
    }
  });
});

describe("Sheet", () => {
  it("关闭时不渲染任何弹层", () => {
    render(
      <Sheet open={false} onClose={vi.fn()}>
        内容
      </Sheet>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("渲染内容与吸底操作，并自动聚焦", async () => {
    render(
      <Sheet open onClose={vi.fn()} label="岗位详情" footer={<button type="button">操作</button>}>
        <p>Sheet 内容</p>
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog", { name: "岗位详情" });
    expect(screen.getByText("Sheet 内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "操作" })).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(dialog));
  });

  it("点遮罩或按 Esc 关闭", () => {
    const onClose = vi.fn();
    render(
      <Sheet open onClose={onClose}>
        内容
      </Sheet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭弹层" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("往下拖超过 130px 时关闭", () => {
    const onClose = vi.fn();
    render(
      <Sheet open onClose={onClose}>
        内容
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.pointerDown(dialog, { pointerId: 1, clientY: 10, button: 0 });
    fireEvent.pointerMove(dialog, { pointerId: 1, clientY: 260 });
    expect(dialog.style.transform).toContain("translateY");
    fireEvent.pointerUp(dialog, { pointerId: 1, clientY: 260 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("轻微下拉会弹回，不关闭", () => {
    const onClose = vi.fn();
    render(
      <Sheet open onClose={onClose}>
        内容
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.pointerDown(dialog, { pointerId: 1, clientY: 10, button: 0 });
    fireEvent.pointerMove(dialog, { pointerId: 1, clientY: 40 });
    fireEvent.pointerUp(dialog, { pointerId: 1, clientY: 40 });
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog.style.transform).toBe("");
  });

  it("Tab 在弹层内循环，不会跑到背后的页面", () => {
    render(
      <Sheet open onClose={vi.fn()}>
        <button type="button">第一个</button>
        <button type="button">最后一个</button>
      </Sheet>,
    );
    const first = screen.getByRole("button", { name: "第一个" });
    const last = screen.getByRole("button", { name: "最后一个" });
    Object.defineProperty(first, "offsetParent", { value: document.body });
    Object.defineProperty(last, "offsetParent", { value: document.body });
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});

describe("ActionSheet", () => {
  it("列出选项与勾选态，选择后回调 value", () => {
    const onSelect = vi.fn();
    render(
      <ActionSheet
        open
        title="修改阶段"
        options={[
          { label: "已投递", value: "applied", checked: true },
          { label: "删除", value: "delete", destructive: true },
        ]}
        onSelect={onSelect}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("修改阶段")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(onSelect).toHaveBeenCalledWith("delete");
    expect(screen.getByRole("button", { name: /已投递/ }).className).toContain("checked");
  });

  it("取消按钮和 Esc 都走 onCancel", () => {
    const onCancel = vi.fn();
    render(<ActionSheet open options={[]} onSelect={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(2);
  });
});

describe("ConfirmDialog", () => {
  it("确认与取消分别回调，关闭时不渲染", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ConfirmDialog open message="确定删除？" onConfirm={onConfirm} onCancel={onCancel} />,
    );
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    rerender(<ConfirmDialog open={false} message="确定删除？" onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
