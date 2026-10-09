import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type SettingsView = components["schemas"]["SettingsView"];
export type SettingsForm = components["schemas"]["SettingsForm"];

async function fetchSettings(): Promise<SettingsView | null> {
  const { data, error, response } = await api.GET("/api/settings");
  if (error) {
    // 走共享漏斗取可读原因（后端 detail 是写给用户看的），别手写固定文案吞掉它。
    throw new Error(apiErrorMessage(error, "加载配置失败", response));
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
      const { data, error, response } = await api.POST("/api/settings/test");
      if (error || !data) {
        // 探测失败的原因（连不上 / 401 / 模型名不对）后端写在 detail 里 ——
        // 手写「探测失败」会把它整段吞掉。注意端点本身对「探测不通过」是
        // 200 + ok:false（不是错误），走到这里的都是**请求层**的失败。
        throw new Error(apiErrorMessage(error, "探测失败", response));
      }
      return data;
    },
  });
}
