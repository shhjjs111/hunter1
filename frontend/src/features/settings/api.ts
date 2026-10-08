import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
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
        // 校验失败时后端给的是可读原因（detail）—— 原样交给用户，别吞掉。
        // ⚠ 只接受**字符串** detail：契约里 PUT /api/settings 的 422 是
        // HTTPValidationError{detail: ValidationError[]}，结构化 detail 直接塞进
        // Error 会变成 "[object Object]"。当前后端用 _readable() 压成字符串，
        // 但这里不该依赖它 —— 形状一变就会退化，所以显式判类型。
        const detail = (error as { detail?: unknown } | undefined)?.detail;
        const message = typeof detail === "string" && detail.trim() !== "" ? detail : null;
        throw new Error(message ?? `保存失败（HTTP ${response.status}）`);
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
