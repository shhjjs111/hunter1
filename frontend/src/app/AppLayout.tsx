import { useEffect, useLayoutEffect, useRef, type CSSProperties } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import appIcon from "../assets/app-icon.png";
import envDark from "../assets/env-dark.webp";
import envLight from "../assets/env-light.webp";

import { useCrawlStatus } from "../features/crawl/api";
import { ErrorBoundary } from "./ErrorBoundary";
import { MobileShell } from "./MobileShell";
import { NAV, NavIcon, navIndex } from "./navigation";
import { useTheme } from "./theme";
import { useIsMobile } from "./useMediaQuery";

/**
 * 应用外壳 —— 宽屏两栏（左侧导航 + 内容区），窄屏单栏（顶部横条导航）。
 *
 * 导航项与后端切片**同名同序**（jobs / crawl / applications / assistant）——
 * 看界面能推断代码在哪，看代码能推断界面长什么样。
 *
 * **为什么要响应式**：本工具虽然绑 127.0.0.1，但要跑在**各种电脑**上 ——
 * 1366 屏开 150% 缩放只剩 911 CSS px、175% 只剩 780 px（Windows 笔记本的
 * 125%/150% 缩放是出厂默认），此时 192px 的固定侧栏会吃掉近三成宽度，
 * 表格被挤到文字竖排。所以 md 以下改为顶部横条。
 */
/**
 * 常驻订阅抓取状态 —— 只订阅，不渲染。
 *
 * 抓取跑在**后端线程**里，用户完全可以离开抓取页。原先「一轮跑完 → 失效岗位库」
 * 的判定只在 `CrawlPage` 取数时发生：那个页面一卸载就没人感知，用户随后打开岗位库
 * 会在 staleTime（30s）内看到**本轮之前**的列表，看起来像「抓了但没进来」
 * （报告次要项）。
 *
 * 单独拆成一个组件是为了把重渲染**隔离**在这里：`useQuery` 的状态变化只让这个
 * 返回 null 的组件重渲染，而不是整棵 `AppLayout` 子树。空闲时它不轮询
 * （见 `crawlPollInterval`），所以常驻的代价接近零。
 */
function CrawlStatusWatcher() {
  useCrawlStatus();
  return null;
}

/**
 * 空间窗口：指针在窗口上移动时，窗口朝指针方向微倾（≤1.6°）并带一块跟随的镜面反光。
 * 直接写 CSS 变量而不走 React state —— 指针事件每秒几十次，不该触发重渲染。
 */
function useSpatialTilt() {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof window.matchMedia !== "function") {
      return;
    }
    const fine = window.matchMedia("(pointer: fine)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!fine || reduced) {
      return;
    }
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = el.getBoundingClientRect();
        const px = (event.clientX - rect.left) / rect.width;
        const py = (event.clientY - rect.top) / rect.height;
        el.style.setProperty("--ry", `${((px - 0.5) * 3.2).toFixed(2)}deg`);
        el.style.setProperty("--rx", `${((0.5 - py) * 2.4).toFixed(2)}deg`);
        el.style.setProperty("--mx", `${(px * 100).toFixed(1)}%`);
        el.style.setProperty("--my", `${(py * 100).toFixed(1)}%`);
        el.style.setProperty("--glare", "1");
      });
    };
    const onLeave = () => {
      cancelAnimationFrame(frame);
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
      el.style.setProperty("--glare", "0");
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
    };
  }, []);
  return ref;
}

/** 环境全景每跨一个模块平移的距离（vw）。全景宽 130vw、左侧预留 5vw，5 个模块共移 20vw，两边都不会露底。 */
const ENV_STEP = 5;

/**
 * 发起一次模块切换前调用：写下方向，主窗里新旧内容按这个方向交叠滑换。
 * （环境全景与两侧小窗不用它 —— 它们直接跟着 `current` 用 CSS transition 走。）
 */
function prepareTransition(from: number, to: number) {
  document.documentElement.dataset.navDir = to < from ? "prev" : "next";
}

/**
 * 舞台上的小窗：每个模块一扇，沿一条弧线排开（当前模块那扇藏在主窗正后方）。
 *
 * 切模块时它们**不**参与 View Transition —— 位置只由 `data-offset` 决定，
 * 交给普通的 CSS transition 去补间。每个状态的 transform 都是同一串函数
 * （translate3d → rotateY → scale），浏览器逐项插值，不会像矩阵分解那样在
 * 透视下「炸开」。离开的模块从主窗后面滑出到弧线上，目标模块沿弧线飞进主窗。
 */
function GhostWindows({ current }: { current: number }) {
  const navigate = useNavigate();
  return (
    <div className="stage" aria-hidden="true">
      {NAV.map((item, index) => {
        const offset = index - current;
        return (
          <div
            key={item.to}
            className="ghost flex flex-col p-6"
            data-offset={Math.max(-3, Math.min(3, offset))}
            onClick={() => {
              if (offset === 0) {
                return;
              }
              prepareTransition(current, index);
              void navigate(item.to, { viewTransition: true });
            }}
          >
            <span className="grid size-12 place-items-center rounded-2xl bg-fill text-ink">
              <NavIcon name={item.icon} className="size-6" />
            </span>
            <span className="mt-5 text-xl font-semibold">{item.label}</span>
            <span className="mt-1 text-sm text-muted">{item.hint}</span>
            <span className="mt-8 flex flex-col gap-3">
              <span className="h-3 w-4/5 rounded-full bg-fill" />
              <span className="h-3 w-3/5 rounded-full bg-fill" />
              <span className="h-3 w-2/3 rounded-full bg-fill" />
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * 导航选中胶囊：整条导航只有一颗，用 CSS 变量把它摆到当前项下面，transition 带回弹滑过去。
 * （不用 View Transition 的 morph：那样胶囊的快照会盖在导航快照上面，转场中把选中项的字挡住。）
 */
function useNavPill(current: number) {
  const ref = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const nav = ref.current;
    if (!nav) {
      return;
    }
    const place = () => {
      const link = nav.querySelectorAll<HTMLElement>("a")[current];
      if (!link) {
        return;
      }
      nav.style.setProperty("--pill-x", `${link.offsetLeft}px`);
      nav.style.setProperty("--pill-w", `${link.offsetWidth}px`);
    };
    place();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(place);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [current]);
  return ref;
}

function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      aria-label={isDark ? "切换到浅色模式" : "切换到深色模式"}
      title={isDark ? "浅色模式" : "深色模式"}
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        toggle({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
      }}
      className={`glass lift grid size-11 shrink-0 place-items-center rounded-full text-ink ${className}`}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {isDark ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2.5v2M12 19.5v2M21.5 12h-2M4.5 12h-2M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4M18.7 18.7l-1.4-1.4M6.7 6.7 5.3 5.3" />
          </>
        ) : (
          <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
        )}
      </svg>
    </button>
  );
}

function DesktopLayout() {
  // ErrorBoundary 的 key 绑当前路径：路由一变就重建，重置 error 状态。
  const location = useLocation();
  const windowRef = useSpatialTilt();
  const navRef = useNavPill(navIndex(location.pathname));
  const current = navIndex(location.pathname);
  const module = NAV[current].icon;
  const { theme } = useTheme();

  // 模块色调写在 <html> 上：环境色晕、窗口描边、导航图标都读 --tint。
  // 用 layout effect：View Transition 截「新状态」快照前就得生效。
  useLayoutEffect(() => {
    document.documentElement.dataset.module = module;
  }, [module]);

  return (
    <div className="relative min-h-screen">
      {/* 沉浸环境：全景照片随模块平移，见 styles.css 的 .env */}
      <div className="env vt-named" aria-hidden="true">
        <div
          className="env-photo"
          style={
            {
              "--env-image": `url(${theme === "light" ? envLight : envDark})`,
              "--env-x": `${-current * ENV_STEP}vw`,
            } as CSSProperties
          }
        />
      </div>
      <div className="grain" aria-hidden="true" />
      <CrawlStatusWatcher />
      <GhostWindows current={current} />

      {/* 左上角品牌签 + 主题开关：悬浮在空间里的小饰件 */}
      <header className="mx-auto flex max-w-[1040px] items-center justify-between px-3 pt-4 md:px-6 md:pt-6">
        {/* 品牌名用 `<div>` 而不是 `<h1>`：每个页面自己还有一个 `<h1>`（PageHeader）。 */}
        <div className="glass flex items-center gap-2.5 rounded-full py-1.5 pr-4 pl-1.5">
          <img className="size-8 rounded-full object-cover" src={appIcon} alt="" />
          <div className="leading-tight">
            <div className="text-[14px] font-semibold">Hunter1</div>
            <p className="text-[11px] text-muted">求职工作台</p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <div className="mx-auto max-w-[1040px] px-3 pt-4 pb-32 md:px-6 md:pt-6">
        {/* 主窗口：舞台正中的大玻璃窗。切模块时新旧内容在同一扇窗里交叠淡换（见 styles.css 的 win）。 */}
        <main
          ref={windowRef}
          className="vt-window vt-named spatial-window relative z-10 tint-glow glass min-h-[68vh] rounded-[34px] px-5 py-7 md:px-10 md:py-10"
          style={{ viewTransitionName: "win" } as CSSProperties}
        >
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
        <div aria-hidden="true" className="mx-auto mt-3 h-1.5 w-28 rounded-full bg-fill-strong" />
      </div>

      {/* visionOS 的标签栏饰件：悬浮在窗口下方的横向玻璃胶囊。
          `aria-label` 必须给：页内还有别的 `<nav>`（如岗位表的分页）。 */}
      <nav
        ref={navRef}
        aria-label="主导航"
        className="dock vt-named glass fixed inset-x-3 bottom-4 z-30 mx-auto flex max-w-[640px] justify-between gap-1 rounded-full p-1.5 md:bottom-6"
        style={{ viewTransitionName: "nav" } as CSSProperties}
      >
        <span aria-hidden="true" className="nav-pill pill-shadow bg-fill-active" />
        {NAV.map((item, index) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={"end" in item ? item.end : undefined}
            // 交给 View Transitions：浏览器支持时，切模块走「空间舞台」转场
            viewTransition
            onClick={() => prepareTransition(current, index)}
            className={({ isActive }) =>
              `lift relative flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-full px-1 py-1.5 text-[11px] whitespace-nowrap md:flex-row md:justify-center md:gap-2 md:px-3 md:py-2.5 md:text-sm ${
                isActive ? "font-medium text-ink" : "text-muted-strong hover:bg-fill hover:text-ink"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span className={`relative ${isActive ? "text-[var(--tint)]" : ""}`}>
                  <NavIcon name={item.icon} />
                </span>
                <span className="relative">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

/** 断点切换只发生在装配层：手机外壳与桌面外壳各自是一棵独立的树。 */
export function AppLayout() {
  const mobile = useIsMobile();
  return mobile ? <MobileShell /> : <DesktopLayout />;
}
