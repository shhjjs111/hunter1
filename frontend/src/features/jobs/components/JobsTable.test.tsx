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
    render(<JobsTable jobs={[JOB]} onApply={() => {}} onScore={() => {}} />);
    expect(screen.getByText("AI产品经理")).toBeTruthy();
    expect(screen.getByText("字节跳动")).toBeTruthy();
    expect(screen.getByText("88")).toBeTruthy();
  });

  it("未评分的岗位显示「未评分」而不是空白", () => {
    render(<JobsTable jobs={[{ ...JOB, match_score: null }]} onApply={() => {}} onScore={() => {}} />);
    expect(screen.getByText("未评分")).toBeTruthy();
  });

  it("空列表给出明确提示", () => {
    render(<JobsTable jobs={[]} onApply={() => {}} onScore={() => {}} />);
    expect(screen.getByText(/没有找到岗位/)).toBeTruthy();
  });

  it("点击投递回调岗位 id", () => {
    const onApply = vi.fn();
    const onScore = vi.fn();
    render(<JobsTable jobs={[JOB]} onApply={onApply} onScore={onScore} />);
    fireEvent.click(screen.getByRole("button", { name: "记录投递" }));
    expect(onApply).toHaveBeenCalledWith("j1");
  });

  it("记录中：按钮禁用并换文案", () => {
    render(<JobsTable jobs={[JOB]} applyingId="j1" onApply={() => {}} onScore={() => {}} />);
    const button = screen.getByRole("button", { name: "记录中…" });
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("点击评分回调岗位 id", () => {
    const onScore = vi.fn();
    render(<JobsTable jobs={[JOB]} onApply={() => {}} onScore={onScore} />);
    fireEvent.click(screen.getByRole("button", { name: "评分" }));
    expect(onScore).toHaveBeenCalledWith("j1");
  });

  it("评分中：该行按钮禁用并换文案（不误禁投递按钮）", () => {
    render(<JobsTable jobs={[JOB]} scoringId="j1" onApply={() => {}} onScore={() => {}} />);
    const scoring = screen.getByRole("button", { name: "评分中…" });
    expect(scoring.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "记录投递" }).hasAttribute("disabled")).toBe(false);
  });

  it("每行的可访问描述带上该行岗位（屏幕阅读器不该只念一串「评分」）", () => {
    // 列表里每行的按钮同名，光靠名字分不清是哪一行。可访问名保持不变
    // （「评分」—— WCAG 2.5.3 要求可访问名包含可见标签），行信息经
    // `aria-describedby` 补上，两边都不吃亏。
    render(<JobsTable jobs={[JOB]} onApply={() => {}} onScore={() => {}} />);
    for (const name of ["评分", "记录投递"]) {
      const button = screen.getByRole("button", { name });
      const describedBy = button.getAttribute("aria-describedby");
      expect(describedBy, `${name} 缺 aria-describedby`).toBeTruthy();
      const target = document.getElementById(describedBy!);
      expect(target?.textContent).toContain("AI产品经理");
    }
  });
});
