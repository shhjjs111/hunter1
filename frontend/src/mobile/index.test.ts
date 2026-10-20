import { Capacitor } from "@capacitor/core";
import { describe, expect, it, vi } from "vitest";

import { installLocalApi } from "./localApi";
import { installMobileBackend, isMobileApp } from "./index";

vi.mock("./localApi", () => ({ installLocalApi: vi.fn() }));

describe("移动入口", () => {
  it("只在原生平台安装本地 API", () => {
    const install = vi.mocked(installLocalApi);
    install.mockClear();
    vi.spyOn(Capacitor, "isNativePlatform").mockReturnValue(false);
    installMobileBackend();
    expect(install).not.toHaveBeenCalled();

    vi.spyOn(Capacitor, "isNativePlatform").mockReturnValue(true);
    installMobileBackend();
    expect(install).toHaveBeenCalledOnce();
    expect(isMobileApp()).toBe(true);
  });
});