import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { JOBS } from "../../../test-utils";
import { JobCard } from "./JobCard";

function renderCard(overrides: Partial<Parameters<typeof JobCard>[0]> = {}) {
  const props = {
    job: JOBS[0],
    applied: false,
    scoring: false,
    applying: false,
    onOpen: vi.fn(),
    onScore: vi.fn(),
    onApply: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<JobCard {...props} />) };
}

describe("JobCard", () => {
  it("展示标题、公司、城市、来源与分数", () => {
    renderCard();
    expect(screen.getByText("AI产品经理")).toBeTruthy();
    expect(screen.getByText("字节跳动")).toBeTruthy();
    expect(screen.getByText("北京")).toBeTruthy();
    expect(screen.getByText("88")).toBeTruthy();
  });

  it("未评分与抓取失败都有显式标记", () => {
    renderCard({ job: JOBS[1] });
    expect(screen.getByText("未评分")).toBeTruthy();
    expect(screen.getByText("抓取失败")).toBeTruthy();
  });

  it("点正文打开详情，点按钮只触发按钮动作", () => {
    const { props } = renderCard();
    fireEvent.click(screen.getByRole("button", { name: /AI产品经理，字节跳动，查看详情/ }));
    expect(props.onOpen).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "重新评分：AI产品经理" }));
    expect(props.onScore).toHaveBeenCalledTimes(1);
    expect(props.onOpen).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "记录投递：AI产品经理" }));
    expect(props.onApply).toHaveBeenCalledTimes(1);
    expect(props.onOpen).toHaveBeenCalledTimes(1);
  });

  it("键盘 Enter / 空格也能打开详情", () => {
    const { props } = renderCard();
    const card = screen.getByRole("button", { name: /查看详情/ });
    fireEvent.keyDown(card, { key: "Enter" });
    fireEvent.keyDown(card, { key: " " });
    expect(props.onOpen).toHaveBeenCalledTimes(2);
  });

  it("子按钮的键盘事件不会被卡片截获去打开详情", () => {
    const { props } = renderCard();
    const button = screen.getByRole("button", { name: "记录投递：AI产品经理" });
    expect(fireEvent.keyDown(button, { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(button, { key: " " })).toBe(true);
    expect(props.onOpen).not.toHaveBeenCalled();
  });

  it("评分中、记录中、已投递分别禁用对应按钮", () => {
    const { rerender } = renderCard({ scoring: true });
    expect(screen.getByRole("button", { name: "重新评分：AI产品经理" })).toHaveProperty("disabled", true);
    expect(screen.getByText("评分中…")).toBeTruthy();

    rerender(
      <JobCard
        job={JOBS[0]}
        applied
        scoring={false}
        applying={false}
        onOpen={vi.fn()}
        onScore={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "已投递：AI产品经理" })).toHaveProperty("disabled", true);
    expect(screen.getByText("已投递 ✓")).toBeTruthy();
  });
});
