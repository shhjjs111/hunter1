import { useCallback, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";

/**
 * 深色 / 浅色主题。
 *
 * 主题只是 `<html data-theme>` 上的一个属性：颜色全部来自 styles.css 里按主题
 * 覆盖的 CSS 变量，组件不需要知道当前主题。用户的选择存 localStorage；
 * 没选过时跟随系统（`prefers-color-scheme`）。
 *
 * 不用 index.html 内联脚本提前设主题：后端 CSP 是 `script-src 'self'`，内联
 * 脚本会被拦。改在 main.tsx 挂载前同步调用 `applyInitialTheme()`。
 */
export type Theme = "dark" | "light";

const STORAGE_KEY = "hunter1-theme";
const listeners = new Set<() => void>();

function systemTheme(): Theme {
  if (typeof window.matchMedia !== "function") {
    return "dark";
  }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 隐私模式下 localStorage 可能不可写：本次会话内照样生效，只是不记住
  }
  listeners.forEach((notify) => notify());
}

export function applyInitialTheme() {
  document.documentElement.dataset.theme = storedTheme() ?? systemTheme();
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => { finished: Promise<void> };
};

/**
 * 当前主题 + 切换函数。切换时若浏览器支持 View Transitions，新主题会从
 * 触发点（按钮中心）圆形扩散铺满屏幕；不支持或用户要求减少动画时直接切。
 */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "dark" as Theme);

  const toggle = useCallback((origin?: { x: number; y: number }) => {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark";
    const doc = document as ViewTransitionDocument;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!doc.startViewTransition || reduced) {
      setTheme(next);
      return;
    }
    const root = document.documentElement;
    if (origin) {
      root.style.setProperty("--vt-x", `${origin.x}px`);
      root.style.setProperty("--vt-y", `${origin.y}px`);
    }
    root.dataset.vt = "theme";
    const transition = doc.startViewTransition(() => {
      flushSync(() => setTheme(next));
    });
    void transition.finished.finally(() => {
      delete root.dataset.vt;
    });
  }, []);

  return { theme, toggle };
}
