import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type ApplicationSummary = components["schemas"]["ApplicationSummary"];
/** 列表响应整体 —— `total` / `has_more` 是**截断信号**（见后端 router 的 `LIST_LIMIT`）。 */
export type ApplicationList = components["schemas"]["ApplicationListResponse"];
/** 契约里的阶段枚举（后端 `ApplicationStage`）—— 现已进契约，不再是魔法字符串。 */
export type ApplicationStage = components["schemas"]["ApplicationStage"];

export const STAGE_LABELS: Record<string, string> = {
  applied: "已投递",
  written_test: "笔试",
  interview: "面试",
  offer: "Offer",
  rejected: "已拒",
  withdrawn: "已放弃",
};

export const STAGE_ORDER: ApplicationStage[] = [
  "applied",
  "written_test",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

async function fetchApplications(): Promise<ApplicationList> {
  const { data, error, response } = await api.GET("/api/applications");
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载投递记录失败", response));
  }
  // 整份响应（含 `total` / `has_more`）交出去 —— 只留 `items` 会让后端特意算的
  // 截断信号在全链路丢失，页面只能拿 `items.length` 冒充「共 N 条」。
  return data;
}

export function useApplications() {
  return useQuery({ queryKey: ["applications"], queryFn: fetchApplications });
}

export function useChangeStage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { applicationId: string; stage: string; note?: string }) => {
      const { data, error, response } = await api.POST("/api/applications/{application_id}/stage", {
        params: { path: { application_id: input.applicationId } },
        // `<select>` 的值天生是 string，在**这里**收窄为契约枚举即可 ——
        // 调用方的类型不必被 DOM 的宽 string 污染。
        body: { stage: input.stage as ApplicationStage, note: input.note ?? null },
      });
      if (error || !data) {
        throw new Error(apiErrorMessage(error, "改阶段失败", response));
      }
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}

export function useDeleteApplication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (applicationId: string) => {
      const { error, response } = await api.DELETE("/api/applications/{application_id}", {
        params: { path: { application_id: applicationId } },
      });
      if (error) {
        throw new Error(apiErrorMessage(error, "删除失败", response));
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}

/**
 * 记录一次投递。
 *
 * 归 applications feature 而不是 jobs：投递记录的本体在这里，岗位页只是**调用方**
 * （入口挂在 `POST /api/applications`，请求体带 job_id）。
 */
export function useApplyToJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { data, error, response } = await api.POST("/api/applications", {
        body: { job_id: jobId },
      });
      if (error || !data) {
        throw new Error(apiErrorMessage(error, "记录投递失败", response));
      }
      return data;
    },
    onSuccess: () => {
      // 新投递落在 applications；岗位页的投递按钮状态也要跟着刷新
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
