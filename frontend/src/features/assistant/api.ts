import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationMessageView = components["schemas"]["ConversationMessageView"];
/** 列表响应整体 —— `total` / `has_more` 是**截断信号**（见后端 router 的 `LIST_LIMIT`）。 */
export type ConversationList = components["schemas"]["ConversationListResponse"];

export async function fetchConversations(): Promise<ConversationList> {
  const { data, error, response } = await api.GET("/api/assistant/conversations");
  if (error || !data) {
    // 用共享的错误漏斗取可读原因（后端 5xx 的 detail 由它按规则决定是否透出），
    // 不再手写固定文案把原因整段吞掉。
    throw new Error(apiErrorMessage(error, "加载会话失败", response));
  }
  return data;
}

export function useConversations() {
  return useQuery({ queryKey: ["assistant", "conversations"], queryFn: fetchConversations });
}

export async function fetchMessages(conversationId: string): Promise<ConversationMessageView[]> {
  const { data, error, response } = await api.GET("/api/assistant/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } },
  });
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载会话消息失败", response));
  }
  return data;
}

export function useConversationMessages(conversationId: string | null) {
  return useQuery({
    queryKey: ["assistant", "messages", conversationId],
    queryFn: () => fetchMessages(conversationId as string),
    enabled: conversationId !== null,
  });
}

/** 发送后让会话列表与消息失效（新一轮已落库）。 */
export function useRefreshConversations() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["assistant"] });
  };
}
