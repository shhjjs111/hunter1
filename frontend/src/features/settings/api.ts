import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type SettingsView = components["schemas"]["SettingsView"];
export type SettingsForm = components["schemas"]["SettingsForm"];

async function fetchSettings(): Promise<SettingsView | null> {
  const { data, error } = await api.GET("/api/settings");
  if (error) {
    throw new Error("加载配置失败");
  }
  return data ?? null;
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
}

export function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (form: SettingsForm) => {
      const { data, error, response } = await api.PUT("/api/settings", { body: form });
      if (error || !data) {
        // 校验失败时后端给的是可读原因（detail）—— 统一走共享转换，别在这儿手写一份。
        // 后端的 422 detail 是 FastAPI 同款数组（`[{loc, msg, type}]`），
        // `apiErrorMessage` 认得两种形状（字符串 / 数组）。
        throw new Error(apiErrorMessage(error, "保存失败", response));
      }
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}

export function useTestConnection() {
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/settings/test");
      if (error || !data) {
        throw new Error("探测失败");
      }
      return data;
    },
  });
}
