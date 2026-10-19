import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SiteCard } from "./SiteCard";

function site(overrides: Partial<Parameters<typeof SiteCard>[0]["site"]> = {}) {
  return { label: "实习僧", status: "pending", fetched: 0, created: 0, updated: 0, error: null, ...overrides };
}

describe("SiteCard", () => {
  it("等待中不显示统计数字", () => {
    render(<SiteCard site={site()} />);
    expect(screen.getByText("等待中")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("运行中显示脉冲点与统计", () => {
    const { container } = render(
      <SiteCard site={site({ status: "running", fetched: 6, created: 2, updated: 1 })} />,
    );
    expect(screen.getByText("抓取中")).toBeTruthy();
    expect(screen.getByText("抓到 6 · 新增 2 · 更新 1")).toBeTruthy();
    expect(container.querySelector(".mobile-pulse")).toBeTruthy();
  });

  it("失败原因直接显示在卡片上", () => {
    render(<SiteCard site={site({ status: "failed", error: "返回了风控页" })} />);
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("返回了风控页")).toBeTruthy();
  });

  it("未知状态原样展示", () => {
    render(<SiteCard site={site({ status: "paused" })} />);
    expect(screen.getByText("paused")).toBeTruthy();
  });
});
