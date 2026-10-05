import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import type { components } from "../../shared/api/schema";

export type CrawlStatus = components["schemas"]["CrawlStatusResponse"];

const POLL_MS = 1000;

async function fetchStatus(): Promise<CrawlStatus> {
  const { data, error } = await api.GET("/api/crawl/status");
  if (error || !data) {
    throw new Error(`读取抓取进度失败：${JSON.stringify(error ?? "无响应")}`);
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
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/crawl");
      if (error || !data) {
        throw new Error("启动抓取失败");
      }
      return data;
    },
  });
}
