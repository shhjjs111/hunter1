import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";

import { JobsPage } from "../features/jobs/JobsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 本工具是本地单用户：数据不会在后台被别处改，30 秒内不重复请求即可
      staleTime: 30_000,
      retry: 1,
    },
  },
});

// 路由骨架：Wave 5 会把 crawl / assistant / applications 逐个挂上来
const router = createBrowserRouter([{ path: "/", element: <JobsPage /> }]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
