import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyInitialTheme, currentTheme, useTheme } from "./theme";

function Toggle() {
  const { theme, toggle } = useTheme();
  return (
    <button type="button" onClick={() => toggle({ x: 10, y: 20 })}>
      {theme}
    </button>
  );
}

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete (document as { startViewTransition?: unknown }).startViewTransition;
  vi.unstubAllGlobals();
});

describe("theme", () => {
  it("没选过时跟随系统；jsdom 无 matchMedia 时默认深色", () => {
    applyInitialTheme();
    expect(currentTheme()).toBe("dark");
  });

  it("系统偏好浅色时初始为浅色", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("light") }));
    applyInitialTheme();
    expect(currentTheme()).toBe("light");
  });

  it("记住的选择优先于系统偏好", () => {
    window.localStorage.setItem("hunter1-theme", "light");
    applyInitialTheme();
    expect(currentTheme()).toBe("light");
  });

  it("切换会更新 html 属性、界面与存储", () => {
    applyInitialTheme();
    render(<Toggle />);
    fireEvent.click(screen.getByRole("button", { name: "dark" }));
    expect(currentTheme()).toBe("light");
    expect(window.localStorage.getItem("hunter1-theme")).toBe("light");
    expect(screen.getByRole("button", { name: "light" })).toBeTruthy();
  });

  it("支持 View Transitions 时走转场，并在结束后清掉标记", async () => {
    applyInitialTheme();
    let finish: () => void = () => {};
    const finished = new Promise<void>((resolve) => {
      finish = resolve;
    });
    (document as { startViewTransition?: unknown }).startViewTransition = (update: () => void) => {
      update();
      return { finished };
    };
    render(<Toggle />);
    fireEvent.click(screen.getByRole("button", { name: "dark" }));
    expect(document.documentElement.dataset.vt).toBe("theme");
    expect(document.documentElement.style.getPropertyValue("--vt-x")).toBe("10px");
    expect(currentTheme()).toBe("light");
    await act(async () => {
      finish();
      await finished;
    });
    expect(document.documentElement.dataset.vt).toBeUndefined();
  });
});
