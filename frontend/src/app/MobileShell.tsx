import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import envDark from "../assets/env-dark.webp";
import envLight from "../assets/env-light.webp";
import { MobileJobsPage } from "../features/jobs/MobileJobsPage";
import { MobileCrawlPage } from "../features/crawl/MobileCrawlPage";
import { MobileApplicationsPage } from "../features/applications/MobileApplicationsPage";
import { MobileAssistantPage } from "../features/assistant/MobileAssistantPage";
import { MobileSettingsPage } from "../features/settings/MobileSettingsPage";
import { useCrawlStatus } from "../features/crawl/api";
import { MobileIcon } from "../shared/ui/mobile";
import { ErrorBoundary } from "./ErrorBoundary";
import { MobileChromeContext, type ToastTone } from "./mobileChrome";
import { NAV, NavIcon, navIndex } from "./navigation";
import { useTheme } from "./theme";
import { usePager, type PagerDom } from "./usePager";
import { useMediaQuery } from "./useMediaQuery";

const PAGES = [MobileJobsPage, MobileCrawlPage, MobileApplicationsPage, MobileAssistantPage, MobileSettingsPage];

export function MobileShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const crawl = useCrawlStatus();
  const routeIndex = navIndex(location.pathname);
  const knownRoute = NAV.some((item) =>
    item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to),
  );

  const [pageSolid, setPageSolid] = useState<boolean[]>(() => NAV.map(() => false));
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [toast, setToast] = useState<{ id: number; tone: ToastTone; message: string } | null>(null);
  const [toastShown, setToastShown] = useState(false);
  const toastSeq = useRef(0);
  const toastTimer = useRef<number | undefined>(undefined);
  const toastFrame = useRef<number | undefined>(undefined);
  const sceneRef = useRef<HTMLDivElement>(null);

  const onSettle = useCallback(
    (index: number) => {
      const target = NAV[index]?.to;
      if (target && target !== location.pathname) {
        void navigate(target);
      }
    },
    [location.pathname, navigate],
  );
  const shellRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const envRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Array<HTMLElement | null>>([]);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const pillRef = useRef<HTMLSpanElement>(null);
  const dom = useMemo<PagerDom>(
    () => ({
      shell: () => shellRef.current,
      viewport: () => viewportRef.current,
      track: () => trackRef.current,
      env: () => envRef.current,
      page: (index) => pageRefs.current[index] ?? null,
      tab: (index) => tabRefs.current[index] ?? null,
      pill: () => pillRef.current,
    }),
    [],
  );
  const { active, animateTo: animatePagerTo, bind, isAt, scrollToTop } = usePager({
    count: NAV.length,
    index: routeIndex,
    reduced,
    onSettle,
    dom,
  });
  const animateRef = useRef(animatePagerTo);
  useEffect(() => {
    animateRef.current = animatePagerTo;
  }, [animatePagerTo]);
  const animateTo = useCallback((index: number) => animateRef.current(index), []);
  const solid = active === 3 || (pageSolid[active] ?? false);

  const showToast = useCallback((tone: ToastTone, message: string) => {
    toastSeq.current += 1;
    window.clearTimeout(toastTimer.current);
    window.cancelAnimationFrame(toastFrame.current ?? 0);
    setToast({ id: toastSeq.current, tone, message });
    setToastShown(false);
    toastFrame.current = window.requestAnimationFrame(() => setToastShown(true));
    toastTimer.current = window.setTimeout(() => setToast(null), 2400);
  }, []);

  useEffect(
    () => () => {
      window.clearTimeout(toastTimer.current);
      window.cancelAnimationFrame(toastFrame.current ?? 0);
    },
    [],
  );

  useEffect(() => {
    if (sceneRef.current) {
      sceneRef.current.inert = overlayOpen;
    }
  }, [overlayOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      const target = event.target;
      if (target instanceof HTMLElement && target.closest("input,textarea,select,[contenteditable='true']")) {
        return;
      }
      if (event.key === "ArrowRight" && active < NAV.length - 1) {
        event.preventDefault();
        animateTo(active + 1);
      } else if (event.key === "ArrowLeft" && active > 0) {
        event.preventDefault();
        animateTo(active - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, animateTo]);

  const chrome = useMemo(
    () => ({
      animateTo,
      showToast,
      setOverlayOpen,
      drawerOpen,
      setDrawerOpen,
      setKeyboardOpen,
    }),
    [animateTo, drawerOpen, showToast],
  );

  if (!knownRoute) {
    return (
      <div className="mobile-app">
        <div className="mobile-env" aria-hidden="true">
          <div
            className="mobile-env-photo"
            style={{ backgroundImage: `url(${theme === "light" ? envLight : envDark})` }}
          />
        </div>
        <div className="mobile-grain" aria-hidden="true" />
        <div className="mobile-page" style={{ paddingTop: "94px" }}>
          <h1 className="mobile-title">页面不存在</h1>
          <p className="mobile-subtitle">这个地址没有对应的页面。</p>
          <button type="button" className="mobile-button primary" onClick={() => void navigate("/")}>
            回到岗位库
          </button>
        </div>
      </div>
    );
  }

  return (
    <MobileChromeContext.Provider value={chrome}>
      <div
        ref={shellRef}
        className={`mobile-app ${overlayOpen ? "overlay-open" : ""} ${keyboardOpen ? "keyboard-open" : ""}`}
      >
        <div className="mobile-env" aria-hidden="true">
          <div
            ref={envRef}
            className="mobile-env-photo"
            style={{ backgroundImage: `url(${theme === "light" ? envLight : envDark})` }}
          />
        </div>
        <div className="mobile-grain" aria-hidden="true" />

        <div className="mobile-scene" ref={sceneRef}>
          <div className="mobile-viewport" ref={viewportRef} {...bind}>
            <div className="mobile-track" ref={trackRef}>
              {PAGES.map((Page, index) => (
                <section
                  key={NAV[index].to}
                  ref={(node) => {
                    pageRefs.current[index] = node;
                  }}
                  className="mobile-page"
                  style={{ left: `${index * 100}%` }}
                  aria-label={NAV[index].label}
                  aria-hidden={active !== index ? true : undefined}
                  inert={active !== index}
                  onScroll={(event) => {
                    if (index !== active) {
                      return;
                    }
                    const next = event.currentTarget.scrollTop > 36;
                    if (next !== (pageSolid[index] ?? false)) {
                      setPageSolid((prev) => prev.map((value, i) => (i === index ? next : value)));
                    }
                  }}
                >
                  <ErrorBoundary>
                    <Page />
                  </ErrorBoundary>
                </section>
              ))}
            </div>
          </div>

          <header className={`mobile-topbar ${solid ? "solid" : ""} ${active === 3 ? "assistant" : ""}`}>
            <div className="mobile-topbar-bg" aria-hidden="true" />
            {active === 3 ? (
              <button
                type="button"
                className="mobile-icon-button glass"
                aria-label="会话列表"
                aria-expanded={drawerOpen}
                onClick={() => setDrawerOpen(true)}
              >
                <MobileIcon name="menu" className="size-[19px]" />
              </button>
            ) : (
              <div className="mobile-brand glass">
                <span className="mobile-brand-logo">H</span>
                <div>
                  <b>Hunter1</b>
                  <small>求职工作台</small>
                </div>
              </div>
            )}
            <div className="mobile-center-title">{NAV[active].label}</div>
            <button
              type="button"
              className="mobile-icon-button glass"
              aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                toggle({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
              }}
            >
              <MobileIcon name={theme === "dark" ? "sun" : "moon"} className="size-[19px]" />
            </button>
          </header>
        </div>

        <nav aria-label="主导航" className="mobile-dock glass">
          <span ref={pillRef} className="mobile-dock-pill" aria-hidden="true" />
          {NAV.map((item, index) => (
            <button
              key={item.to}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              type="button"
              className="mobile-dock-tab"
              aria-label={item.label}
              aria-current={active === index ? "page" : undefined}
              onClick={() => {
                if (active === index && isAt(index)) {
                  scrollToTop(index);
                  return;
                }
                animateTo(index);
              }}
            >
              <NavIcon name={item.icon} />
              {index === 1 && (
                <span className={`mobile-dock-badge ${crawl.data?.running ? "show" : ""}`} aria-hidden="true" />
              )}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        {toast && (
          <div
            key={toast.id}
            className={`mobile-toast glass ${toastShown ? "show" : ""} ${toast.tone}`}
            role={toast.tone === "error" ? "alert" : "status"}
            aria-live={toast.tone === "error" ? "assertive" : "polite"}
          >
            {toast.tone === "success" ? <MobileIcon name="check" className="size-4" /> : null}
            <span>{toast.message}</span>
          </div>
        )}
      </div>
    </MobileChromeContext.Provider>
  );
}
