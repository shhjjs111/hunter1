import { useSyncExternalStore } from "react";

function subscribe(query: string, notify: () => void): () => void {
  if (typeof window.matchMedia !== "function") {
    return () => {};
  }
  const list = window.matchMedia(query);
  if (typeof list.addEventListener === "function") {
    list.addEventListener("change", notify);
    return () => list.removeEventListener("change", notify);
  }
  list.addListener?.(notify);
  return () => list.removeListener?.(notify);
}

function matches(query: string): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

/**
 * 媒体查询订阅。没有 matchMedia 的环境（旧浏览器 / jsdom 默认）一律返回 false，
 * 于是 AppLayout 保持桌面外壳 —— 现有桌面测试不会因为新增手机分支而漂移。
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => subscribe(query, notify),
    () => matches(query),
    () => false,
  );
}

export const MOBILE_QUERY = "(max-width: 767px)";

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY);
}
