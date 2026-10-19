import { createContext, useContext } from "react";

export type ToastTone = "success" | "error";

export type MobileChrome = {
  animateTo: (index: number) => void;
  showToast: (tone: ToastTone, message: string) => void;
  setOverlayOpen: (open: boolean) => void;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  setKeyboardOpen: (open: boolean) => void;
};

const noop = () => {};

/** 单个手机页面直接挂载测试时的无操作默认值。 */
export const MobileChromeContext = createContext<MobileChrome>({
  animateTo: noop,
  showToast: noop,
  setOverlayOpen: noop,
  drawerOpen: false,
  setDrawerOpen: noop,
  setKeyboardOpen: noop,
});

export function useMobileChrome(): MobileChrome {
  return useContext(MobileChromeContext);
}
