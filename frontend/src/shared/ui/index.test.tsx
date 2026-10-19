import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorNotice, Pager, SuccessNotice, Toast } from "./index";

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

  it("live region 先空挂载，下一帧才填入文字", () => {
    // 读屏器对「节点插入时已经带文字」的 live region 不保证播报；
    // 空容器先挂进 DOM，下一帧再写入，播报才稳定。
    vi.useFakeTimers();
    render(<Toast tone="success" message="已记录投递。" />);
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");

    act(() => vi.advanceTimersByTime(16));
    expect(status.textContent).toBe("已记录投递。");
  });

  it("多行提示用圆角矩形而不是胶囊，文字不会贴到圆弧上", () => {
    render(<Toast tone="error" message="请求失败：服务返回 401，请检查模型配置。" />);
    const toast = screen.getByRole("alert");
    expect(toast.className).toContain("rounded-3xl");
    expect(toast.className).not.toContain("rounded-full");
    expect(toast.className).toContain("px-5");
    expect(toast.className).toContain("py-3");
    expect(toast.querySelector("span")?.className).toContain("break-words");
  });

  it("固定在 dock 上方，不随页面内容滚走", () => {
    render(<Toast tone="success" message="已记录投递。" />);
    const toast = screen.getByRole("status");
    expect(toast.parentElement).toBe(document.body);
    expect(toast.className).toContain("fixed");
    // 手机端 dock 顶边在 844px 视口的 764px 附近；Toast 底边再留 16px，
    // 100px 的偏移让两者之间保持约 20px，而不是原来的 4px。
    expect(toast.className).toContain("bottom-[100px]");
    expect(toast.className).toContain("md:bottom-[104px]");
  });
});

describe("页面内提示的可访问语义", () => {
  it("错误提示以 alert 暴露给读屏", () => {
    render(<ErrorNotice message="保存失败。" />);
    expect(screen.getByRole("alert").textContent).toContain("保存失败。");
    expect(screen.getByRole("alert").getAttribute("aria-live")).toBe("assertive");
  });

  it("成功提示以 polite status 暴露给读屏", () => {
    render(<SuccessNotice message="保存成功。" />);
    expect(screen.getByRole("status").textContent).toContain("保存成功。");
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  });
});
