import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Pager } from "./index";

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
});
