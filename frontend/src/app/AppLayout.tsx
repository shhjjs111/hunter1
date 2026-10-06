import { NavLink, Outlet } from "react-router";

import { ErrorBoundary } from "./ErrorBoundary";

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
const NAV = [
  { to: "/", label: "岗位库", end: true },
  { to: "/crawl", label: "抓取" },
  { to: "/applications", label: "投递记录" },
  { to: "/assistant", label: "求职助手" },
  { to: "/settings", label: "配置" },
];

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="shrink-0 border-b border-slate-200 bg-white md:w-48 md:border-r md:border-b-0">
        <div className="px-4 pt-3 pb-1 md:py-5">
          <h1 className="text-lg font-semibold tracking-tight">Hunter1</h1>
          <p className="text-xs text-slate-500">求职工作台</p>
        </div>
        {/* 窄屏：横向可滚动的导航条（5 项在 390px 下也放不下，允许横滑）；
            宽屏：恢复成竖向列表。 */}
        <nav className="flex gap-0.5 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible md:pb-4">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `shrink-0 rounded px-3 py-2 text-sm whitespace-nowrap transition-colors ${
                  isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-6 md:py-8">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
