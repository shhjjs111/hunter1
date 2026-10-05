import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Pager } from "./Pager";

describe("Pager", () => {
  it("首页且无下一页时不渲染", () => {
    const { container } = render(<Pager page={1} hasNext={false} onPageChange={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it("首页禁用上一页、可点下一页", () => {
    const onPageChange = vi.fn();
    render(<Pager page={1} hasNext onPageChange={onPageChange} />);
    const previous = screen.getByRole("button", { name: "← 上一页" });
    expect(previous.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("末页可回上一页", () => {
    const onPageChange = vi.fn();
    render(<Pager page={3} hasNext={false} onPageChange={onPageChange} />);
    expect(screen.getByText("第 3 页")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "← 上一页" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
