import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Pager, Toast } from "./index";

afterEach(() => {
  vi.useRealTimers();
});

describe("Pager", () => {
  it("首页且无下一页时不渲染", () => {
    const { container } = render(
      <Pager page={1} hasNext={false} onPageChange={() => {}} ariaLabel="岗位分页" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("首页禁用上一页、可点下一页", () => {
    const onPageChange = vi.fn();
    render(<Pager page={1} hasNext onPageChange={onPageChange} ariaLabel="岗位分页" />);
    const previous = screen.getByRole("button", { name: "← 上一页" });
    expect(previous.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("末页可回上一页", () => {
    const onPageChange = vi.fn();
    render(<Pager page={3} hasNext={false} onPageChange={onPageChange} ariaLabel="岗位分页" />);
    expect(screen.getByText("第 3 页")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "← 上一页" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("可访问名由调用方给 —— 页内多个分页地标才能区分", () => {
    // 没有可访问名时，屏幕阅读器的地标列表里会出现两个无法区分的「navigation」。
    render(<Pager page={2} hasNext onPageChange={() => {}} ariaLabel="会话分页" />);
    expect(screen.getByRole("navigation", { name: "会话分页" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "岗位分页" })).toBeNull();
  });

  it("busy 时两个按钮都不可点 —— 否则连点会翻过末页", () => {
    // 调用方在用 placeholderData 保留上一页时，屏幕上的 `hasNext` 还是上一页的：
    // 不置忙就能连点「下一页」，跳过一整页直接落到末页之后（一页空白）。
    const onPageChange = vi.fn();
    render(<Pager page={2} hasNext busy onPageChange={onPageChange} ariaLabel="岗位分页" />);
    expect(screen.getByRole("button", { name: "下一页 →" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "← 上一页" }).hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    expect(onPageChange).not.toHaveBeenCalled();
  });
});

describe("Toast", () => {
  it("成功提示 4 秒后自动消失", () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(<Toast tone="success" message="已记录投递。" onDismiss={onDismiss} />);

    act(() => vi.advanceTimersByTime(3999));
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("错误提示常驻，只有关闭按钮能收起", () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(<Toast tone="error" message="记录投递失败。" onDismiss={onDismiss} />);

    act(() => vi.advanceTimersByTime(60_000));
    expect(onDismiss).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("固定在 dock 上方，不随页面内容滚走", () => {
    render(<Toast tone="success" message="已记录投递。" />);
    const toast = screen.getByRole("status");
    expect(toast.className).toContain("fixed");
    expect(toast.className).toContain("bottom-[84px]");
    expect(toast.className).toContain("md:bottom-[104px]");
  });
});
