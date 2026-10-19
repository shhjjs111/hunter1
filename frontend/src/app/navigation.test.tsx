import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NAV, NavIcon, navIndex, type NavIconName } from "./navigation";

describe("navigation", () => {
  it("五个模块与后端切片同名同序", () => {
    expect(NAV.map((item) => item.label)).toEqual(["岗位库", "抓取", "投递记录", "求职助手", "配置"]);
  });

  it("navIndex 对根路径、子路径与未知路径都给出稳定序号", () => {
    expect(navIndex("/")).toBe(0);
    expect(navIndex("/crawl")).toBe(1);
    expect(navIndex("/crawl/running")).toBe(1);
    expect(navIndex("/applications/app1")).toBe(2);
    expect(navIndex("/assistant")).toBe(3);
    expect(navIndex("/settings/model")).toBe(4);
    expect(navIndex("/unknown")).toBe(0);
  });

  it("每个导航图标都能渲染", () => {
    const names: NavIconName[] = ["jobs", "crawl", "applications", "assistant", "settings"];
    for (const name of names) {
      const { container, unmount } = render(<NavIcon name={name} />);
      expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
      unmount();
    }
  });
});
