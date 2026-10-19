import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type JobSummary = components["schemas"]["JobSummary"];
export type JobListResponse = components["schemas"]["JobListResponse"];
export type JobDetail = components["schemas"]["JobDetail"];

export const PAGE_SIZE = 20;

export async function fetchJobs(keyword: string, page: number): Promise<JobListResponse> {
  const { data, error, response } = await api.GET("/api/jobs", {
    params: { query: { q: keyword, page, page_size: PAGE_SIZE } },
  });
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载岗位失败", response));
  }
  return data;
}

/** 手机端滚动加载：同一端点、同一 PAGE_SIZE，只是把分页串成一条无限列表。 */
export function useInfiniteJobs(keyword: string) {
  return useInfiniteQuery({
    queryKey: ["jobs", "infinite", { keyword }],
    queryFn: ({ pageParam }) => fetchJobs(keyword, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_next ? last.page + 1 : undefined),
  });
}

async function fetchJobDetail(jobId: string): Promise<JobDetail> {
  const { data, error, response } = await api.GET("/api/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载岗位详情失败", response));
  }
  return data;
}

export function useJobDetail(jobId: string | null) {
  return useQuery({
    queryKey: ["jobs", "detail", jobId],
    queryFn: () => fetchJobDetail(jobId as string),
    enabled: jobId !== null,
  });
}

export function useJobs(keyword: string, page: number) {
  return useQuery({
    queryKey: ["jobs", { keyword, page }],
    queryFn: () => fetchJobs(keyword, page),
    // 翻页/换关键词时保留上一页内容，避免闪白
    placeholderData: (previous) => previous,
  });
}
