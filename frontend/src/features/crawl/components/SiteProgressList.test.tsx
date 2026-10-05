import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { components } from "../../../shared/api/schema";
import { SiteProgressList } from "./SiteProgressList";

type SiteProgress = components["schemas"]["SiteProgressView"];

function site(overrides: Partial<SiteProgress> = {}): SiteProgress {
  return {
    label: "实习僧",
    status: "ok",
    fetched: 20,
    created: 18,
    updated: 2,
    error: null,
    ...overrides,
  };
}

describe("SiteProgressList", () => {
  it("渲染站点名与计数", () => {
    render(<SiteProgressList sites={[site()]} />);
    expect(screen.getByText("实习僧")).toBeTruthy();
    expect(screen.getByText("20")).toBeTruthy();
    expect(screen.getByText("18")).toBeTruthy();
  });

  it("状态映射成中文标签", () => {
    render(
      <SiteProgressList
        sites={[
          site({ label: "甲", status: "running" }),
          site({ label: "乙", status: "failed", error: "boom" }),
          site({ label: "丙", status: "pending" }),
        ]}
      />,
    );
    expect(screen.getByText("抓取中")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("等待中")).toBeTruthy();
  });

  it("未知状态原样显示（不静默成空白）", () => {
    render(<SiteProgressList sites={[site({ status: "weird" })]} />);
    expect(screen.getByText("weird")).toBeTruthy();
  });
});
