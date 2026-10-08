import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router";

import { AppLayout } from "./AppLayout";
import { ApplicationsPage } from "../features/applications/ApplicationsPage";
import { AssistantPage } from "../features/assistant/AssistantPage";
import { CrawlPage } from "../features/crawl/CrawlPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { SettingsPage } from "../features/settings/SettingsPage";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 本工具是本地单用户：数据不会在后台被别处改，30 秒内不重复请求即可
        staleTime: 30_000,
        retry: 1,
      },
    },
  });
}

/**
 * 路由表 —— **导出成数据**而不是直接建 router。
 *
 * 好处：测试可以用 memory router 挂同一份路由表（走进真实的路由匹配与布局），
 * 而 `createBrowserRouter` 在模块加载时就固定了初始 URL，测试里改 URL 不会
 * 触发它重新匹配 —— 那样只能测到「布局渲染了，页面内容没渲染」。
 */
export const appRoutes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <JobsPage /> },
      { path: "crawl", element: <CrawlPage /> },
      { path: "applications", element: <ApplicationsPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
];

export function AppShell({ router }: { router: ReturnType<typeof createBrowserRouter> }) {
  // queryClient 惰性初始化，**只建一次**：在 render 里直接 makeQueryClient()
  // 会每次渲染都换新实例（StrictMode 下更明显），所有查询缓存随之被丢弃重取。
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

export function App() {
  // 同理：router 在 render 里创建过 —— 每次渲染重建会重置导航状态。
  const [router] = useState(() => createBrowserRouter(appRoutes));
  return <AppShell router={router} />;
}
