import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import type { components } from "../../shared/api/schema";

export type CandidateProfile = components["schemas"]["CandidateProfile"];
export type ProfileForm = components["schemas"]["ProfileForm"];
export type ProfileView = components["schemas"]["ProfileView"];
export type ScoreView = components["schemas"]["ScoreView"];

async function fetchProfile(): Promise<CandidateProfile | null> {
  const { data, error } = await api.GET("/api/scoring/profile");
  if (error) {
    throw new Error(`加载画像失败：${JSON.stringify(error)}`);
  }
  // 「没配过」是 null，不是错误 —— 初始状态
  return data?.profile ?? null;
}

export function useProfile() {
  return useQuery({ queryKey: ["scoring", "profile"], queryFn: fetchProfile });
}

export function useSaveProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (form: ProfileForm) => {
      const { data, error, response } = await api.PUT("/api/scoring/profile", { body: form });
      if (error || !data) {
        // 后端在 422 里给了可读原因（画像不能为空），原样交给用户
        const detail = (error as { detail?: string } | undefined)?.detail;
        throw new Error(detail ?? `保存画像失败（HTTP ${response.status}）`);
      }
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["scoring", "profile"] });
    },
  });
}

/**
 * 给一个岗位评分。
 *
 * 未配置画像时后端返回 409 + 修复指引 —— 原样透出给用户，
 * 而不是替换成「评分失败」这种丢掉关键信息的措辞。
 */
export function useScoreJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { data, error, response } = await api.POST("/api/scoring/{job_id}", {
        params: { path: { job_id: jobId } },
      });
      if (error || !data) {
        const detail = (error as { detail?: string } | undefined)?.detail;
        throw new Error(detail ?? `评分失败（HTTP ${response.status}）`);
      }
      return data;
    },
    onSuccess: () => {
      // 分数写回了岗位 —— 刷新岗位列表让「匹配分」列更新
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
