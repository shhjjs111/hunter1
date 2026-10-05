import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import type { components } from "../../shared/api/schema";

export type JobSummary = components["schemas"]["JobSummary"];
export type JobListResponse = components["schemas"]["JobListResponse"];

export const PAGE_SIZE = 20;

async function fetchJobs(keyword: string, page: number): Promise<JobListResponse> {
  const { data, error } = await api.GET("/api/jobs", {
    params: { query: { q: keyword, page, page_size: PAGE_SIZE } },
  });
  if (error || !data) {
    throw new Error(`加载岗位失败：${JSON.stringify(error ?? "无响应")}`);
  }
  return data;
}

export function useJobs(keyword: string, page: number) {
  return useQuery({
    queryKey: ["jobs", { keyword, page }],
    queryFn: () => fetchJobs(keyword, page),
    // 翻页/换关键词时保留上一页内容，避免闪白
    placeholderData: (previous) => previous,
  });
}

export function useApplyToJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { data, error, response } = await api.POST("/api/jobs/{job_id}/apply", {
        params: { path: { job_id: jobId } },
      });
      if (error || !data) {
        throw new Error(`记录投递失败（HTTP ${response.status}）`);
      }
      return data;
    },
    onSuccess: () => {
      // 投递记录会出现在 applications feature —— 先只刷新本切片的查询
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
