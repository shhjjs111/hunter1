import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type CandidateProfile = components["schemas"]["CandidateProfile"];
export type ProfileForm = components["schemas"]["ProfileForm"];
export type ProfileView = components["schemas"]["ProfileView"];
export type ScoreView = components["schemas"]["ScoreView"];

async function fetchProfile(): Promise<ProfileView> {
  const { data, error, response } = await api.GET("/api/scoring/profile");
  if (error) {
    throw new Error(apiErrorMessage(error, "加载画像失败", response));
  }
  // 返回整个视图而不是只取 profile：`warning` 要说清「存储里的画像不可用」，
  // 只取 profile 会把它丢掉，变成静默的「没配过」。
  return data ?? { profile: null, warning: null };
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
        throw new Error(apiErrorMessage(error, "保存画像失败", response));
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
        throw new Error(apiErrorMessage(error, "评分失败", response));
      }
      return data;
    },
    onSuccess: () => {
      // 分数写回了岗位 —— 刷新岗位列表让「匹配分」列更新
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
