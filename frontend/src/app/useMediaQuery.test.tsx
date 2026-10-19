import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MOBILE_QUERY, useIsMobile, useMediaQuery } from "./useMediaQuery";

function Probe() {
  return (
    <p>
      {String(useIsMobile())}:{String(useMediaQuery("(prefers-reduced-motion: reduce)"))}
    </p>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMediaQuery", () => {
  it("没有 matchMedia 时一律返回 false（jsdom / 旧浏览器不误判手机端）", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<Probe />);
    expect(screen.getByText("false:false")).toBeTruthy();
  });

  it("订阅 change 事件并跟随匹配结果更新", () => {
    let matched = false;
    const listeners: Array<() => void> = [];
    vi.stubGlobal("matchMedia", (query: string) => ({
      get matches() {
        return query === MOBILE_QUERY || query.includes("reduced") ? matched : false;
      },
      media: query,
      addEventListener: (_event: string, callback: () => void) => listeners.push(callback),
      removeEventListener: (_event: string, callback: () => void) => {
        const index = listeners.indexOf(callback);
        if (index >= 0) {
          listeners.splice(index, 1);
        }
      },
    }));
    const { unmount } = render(<Probe />);
    expect(screen.getByText("false:false")).toBeTruthy();

    act(() => {
      matched = true;
      for (const listener of [...listeners]) {
        listener();
      }
    });
    expect(screen.getByText("true:true")).toBeTruthy();
    expect(listeners.length).toBeGreaterThan(0);

    unmount();
    expect(listeners).toHaveLength(0);
  });

  it("兼容只提供 addListener 的旧版 MediaQueryList", () => {
    let matched = false;
    const listeners: Array<() => void> = [];
    vi.stubGlobal("matchMedia", (query: string) => ({
      get matches() {
        return matched && query === MOBILE_QUERY;
      },
      media: query,
      addListener: (callback: () => void) => listeners.push(callback),
      removeListener: (callback: () => void) => {
        const index = listeners.indexOf(callback);
        if (index >= 0) {
          listeners.splice(index, 1);
        }
      },
    }));
    const { unmount } = render(<Probe />);
    act(() => {
      matched = true;
      for (const listener of [...listeners]) {
        listener();
      }
    });
    expect(screen.getByText("true:false")).toBeTruthy();
    unmount();
    expect(listeners).toHaveLength(0);
  });
});
