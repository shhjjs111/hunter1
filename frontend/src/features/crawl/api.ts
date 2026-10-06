import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type CrawlStatus = components["schemas"]["CrawlStatusResponse"];

const POLL_MS = 1000;

async function fetchStatus(): Promise<CrawlStatus> {
  const { data, error, response } = await api.GET("/api/crawl/status");
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "读取抓取进度失败", response));
  }
  return data;
}

/**
 * 抓取进度。**只在抓取进行中轮询** —— 空闲时每分钟一次就够，没必要每秒打后端。
 * （旧界面对快照的用法一致：开始抓取后才密集拉。）
 */
export function useCrawlStatus() {
  return useQuery({
    queryKey: ["crawl", "status"],
    queryFn: fetchStatus,
    refetchInterval: (query) => (query.state.data?.running ? POLL_MS : false),
  });
}

export function useStartCrawl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/crawl");
      if (error || !data) {
        throw new Error("启动抓取失败");
      }
      return data;
    },
    onSuccess: () => {
      // 必须重取状态：缓存里还是 `running: false`，而上面 useCrawlStatus 的
      // refetchInterval 只在 running 时轮询 —— 不重取就永远转不起来，
      // 界面最长 staleTime（30 秒）内毫无反应，按钮还恢复可点，
      // 用户再点只会看到「上一轮还在跑」。
      //
      // 立刻重取就能拿到 `running: true`：后端 `CrawlRunner.start()` 是**同步**
      // 置位后再返回的（见 slices/crawl/runner.py），所以这里没有竞态窗口。
      void queryClient.invalidateQueries({ queryKey: ["crawl", "status"] });
    },
  });
}
