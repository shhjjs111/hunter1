import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import type { components } from "../../shared/api/schema";

export type ApplicationSummary = components["schemas"]["ApplicationSummary"];

export const STAGE_LABELS: Record<string, string> = {
  applied: "已投递",
  written_test: "笔试",
  interview: "面试",
  offer: "Offer",
  rejected: "已拒",
  withdrawn: "已放弃",
};

export const STAGE_ORDER = [
  "applied",
  "written_test",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

async function fetchApplications(): Promise<ApplicationSummary[]> {
  const { data, error } = await api.GET("/api/applications");
  if (error || !data) {
    throw new Error("加载投递记录失败");
  }
  return data.items;
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
        body: { stage: input.stage, note: input.note ?? null },
      });
      if (error || !data) {
        throw new Error(`改阶段失败（HTTP ${response.status}）`);
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
        throw new Error(`删除失败（HTTP ${response.status}）`);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}
