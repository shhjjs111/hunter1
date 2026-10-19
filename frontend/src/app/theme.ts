import { useCallback, useEffect, useSyncExternalStore } from "react";
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
export type ThemePreference = Theme | "system";

const STORAGE_KEY = "hunter1-theme";
const listeners = new Set<() => void>();

function systemTheme(): Theme {
  if (typeof window.matchMedia !== "function") {
    return "dark";
  }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function storedPreference(): ThemePreference | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : null;
  } catch {
    return null;
  }
}

function resolvedTheme(preference: ThemePreference): Theme {
  return preference === "system" ? systemTheme() : preference;
}

export function currentPreference(): ThemePreference {
  return storedPreference() ?? "system";
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function paint(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  listeners.forEach((notify) => notify());
}

function persist(preference: ThemePreference) {
  try {
    window.localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // 隐私模式下 localStorage 可能不可写：本次会话内照样生效，只是不记住
  }
}

export function applyInitialTheme() {
  const preference = storedPreference() ?? "system";
  document.documentElement.dataset.theme = resolvedTheme(preference);
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
  const preference = storedPreference() ?? "system";

  useEffect(() => {
    if (preference !== "system" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => paint(systemTheme());
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    }
    media.addListener?.(onChange);
    return () => media.removeListener?.(onChange);
  }, [preference]);

  const setPreference = useCallback((preference: ThemePreference, origin?: { x: number; y: number }) => {
    const next = resolvedTheme(preference);
    persist(preference);
    const doc = document as ViewTransitionDocument;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (currentTheme() === next) {
      listeners.forEach((notify) => notify());
      return;
    }
    if (!doc.startViewTransition || reduced) {
      paint(next);
      return;
    }
    const root = document.documentElement;
    if (origin) {
      root.style.setProperty("--vt-x", `${origin.x}px`);
      root.style.setProperty("--vt-y", `${origin.y}px`);
    }
    root.dataset.vt = "theme";
    const transition = doc.startViewTransition(() => {
      flushSync(() => paint(next));
    });
    void transition.finished.finally(() => {
      delete root.dataset.vt;
    });
  }, []);

  const toggle = useCallback((origin?: { x: number; y: number }) => {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark";
    setPreference(next, origin);
  }, [setPreference]);

  return { theme, preference, toggle, setPreference };
}
