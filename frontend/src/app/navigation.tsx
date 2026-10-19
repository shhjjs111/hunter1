import type { ReactNode } from "react";

/** 导航项与后端切片同名同序（jobs / crawl / applications / assistant / settings）。 */
export const NAV = [
  { to: "/", label: "岗位库", end: true, icon: "jobs", hint: "抓回来的岗位与匹配分" },
  { to: "/crawl", label: "抓取", icon: "crawl", hint: "从公开站点拉取新岗位" },
  { to: "/applications", label: "投递记录", icon: "applications", hint: "每一次投递的进展" },
  { to: "/assistant", label: "求职助手", icon: "assistant", hint: "备面试、改简历、比 offer" },
  { to: "/settings", label: "配置", icon: "settings", hint: "模型与求职画像" },
] as const;

export type NavIconName = (typeof NAV)[number]["icon"];

/** 模块色（RGB 分量）—— 手机端按滑动进度线性插值。 */
export const MODULE_TINTS = [
  [10, 132, 255],
  [50, 214, 160],
  [255, 159, 10],
  [191, 90, 242],
  [94, 156, 255],
] as const;

/** SF Symbols 风格的线性图标（1.75 描边、圆角端点），纯装饰，读屏忽略。 */
export function NavIcon({
  name,
  className = "size-[22px] shrink-0",
}: {
  name: NavIconName;
  className?: string;
}) {
  const paths: Record<NavIconName, ReactNode> = {
    jobs: (
      <>
        <rect x="3.5" y="7" width="17" height="12.5" rx="3" />
        <path d="M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7" />
        <path d="M3.5 12.5h17" />
      </>
    ),
    crawl: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M3.5 12h17M12 3.5c2.4 2.3 3.6 5.1 3.6 8.5s-1.2 6.2-3.6 8.5c-2.4-2.3-3.6-5.1-3.6-8.5S9.6 5.8 12 3.5Z" />
      </>
    ),
    applications: (
      <>
        <path d="M20.5 3.5 10 14" />
        <path d="M20.5 3.5 14 20.5l-4-6.5-6.5-4 17-6.5Z" />
      </>
    ),
    assistant: (
      <>
        <path d="M12 3.5l1.6 4.4 4.4 1.6-4.4 1.6L12 15.5l-1.6-4.4L6 9.5l4.4-1.6L12 3.5Z" />
        <path d="M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

/** 当前路径对应的导航序号 —— 用来判断转场方向（往后的模块 / 往前的模块）。 */
export function navIndex(pathname: string): number {
  const index = NAV.findIndex((item) =>
    item.to === "/" ? pathname === "/" : pathname.startsWith(item.to),
  );
  return index === -1 ? 0 : index;
}
