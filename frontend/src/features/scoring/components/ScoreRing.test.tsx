import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScoreRing } from "./ScoreRing";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ScoreRing", () => {
  it("未评分时显示破折号与无障碍说明", () => {
    render(<ScoreRing score={null} />);
    const ring = screen.getByRole("img", { name: "还没有评分" });
    expect(ring.textContent).toBe("—");
  });

  it("减少动态效果时直接显示目标分数", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduced"), media: query }));
    render(<ScoreRing score={91} />);
    expect(screen.getByRole("img", { name: "匹配分 91" }).textContent).toBe("91");
  });

  it("普通模式从 0 滚动到目标分数", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query }));
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("performance", { now: () => 1000 });

    render(<ScoreRing score={86} />);
    expect(screen.getByRole("img", { name: "匹配分 86" }).textContent).toBe("0");

    act(() => {
      frames.shift()?.(1900);
    });
    expect(screen.getByRole("img", { name: "匹配分 86" }).textContent).toBe("86");
  });
});
