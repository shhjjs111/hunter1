import { NavLink, Outlet } from "react-router";

import { ErrorBoundary } from "./ErrorBoundary";

/**
 * 应用外壳 —— 两栏布局：左侧导航 + 内容区。
 *
 * 导航项与后端切片**同名同序**（jobs / crawl / applications / assistant）——
 * 看界面能推断代码在哪，看代码能推断界面长什么样。
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
    <div className="flex min-h-screen">
      <aside className="w-48 shrink-0 border-r border-slate-200 bg-white">
        <div className="px-4 py-5">
          <h1 className="text-lg font-semibold tracking-tight">Hunter1</h1>
          <p className="text-xs text-slate-500">求职工作台</p>
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded px-3 py-2 text-sm transition-colors ${
                  isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 px-6 py-8">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
