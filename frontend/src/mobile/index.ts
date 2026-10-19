import { Capacitor } from "@capacitor/core";

import { installLocalApi } from "./localApi";

/** 安卓独立 App：把所有 `/api/*` 请求接到本地 IndexedDB 实现。 */
export function installMobileBackend(): void {
  if (Capacitor.isNativePlatform()) {
    installLocalApi();
  }
}

export function isMobileApp(): boolean {
  return Capacitor.isNativePlatform();
}