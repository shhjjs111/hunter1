import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { JobSummary } from "../api";
import { JobsTable } from "./JobsTable";

const JOB: JobSummary = {
  id: "j1",
  title: "AI产品经理",
  company: "字节跳动",
  city: "北京",
  match_score: 88,
  source: "实习僧",
  capture_status: "complete",
  detail_url: "https://x/1",
  last_seen_at: null,
};

describe("JobsTable", () => {
  it("渲染岗位、公司名与匹配分", () => {
    render(<JobsTable jobs={[JOB]} onApply={() => {}} />);
    expect(screen.getByText("AI产品经理")).toBeTruthy();
    expect(screen.getByText("字节跳动")).toBeTruthy();
    expect(screen.getByText("88")).toBeTruthy();
  });

  it("未评分的岗位显示「未评分」而不是空白", () => {
    render(<JobsTable jobs={[{ ...JOB, match_score: null }]} onApply={() => {}} />);
    expect(screen.getByText("未评分")).toBeTruthy();
  });

  it("空列表给出明确提示", () => {
    render(<JobsTable jobs={[]} onApply={() => {}} />);
    expect(screen.getByText(/没有找到岗位/)).toBeTruthy();
  });

  it("点击投递回调岗位 id", () => {
    const onApply = vi.fn();
    render(<JobsTable jobs={[JOB]} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: "记录投递" }));
    expect(onApply).toHaveBeenCalledWith("j1");
  });

  it("记录中：按钮禁用并换文案", () => {
    render(<JobsTable jobs={[JOB]} applyingId="j1" onApply={() => {}} />);
    const button = screen.getByRole("button", { name: "记录中…" });
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});
