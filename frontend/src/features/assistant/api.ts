import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import type { components } from "../../shared/api/schema";

export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationMessageView = components["schemas"]["ConversationMessageView"];

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const { data, error } = await api.GET("/api/assistant/conversations");
  if (error || !data) {
    throw new Error("加载会话失败");
  }
  return data;
}

export function useConversations() {
  return useQuery({ queryKey: ["assistant", "conversations"], queryFn: fetchConversations });
}

export async function fetchMessages(conversationId: string): Promise<ConversationMessageView[]> {
  const { data, error } = await api.GET("/api/assistant/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } },
  });
  if (error || !data) {
    throw new Error("加载会话消息失败");
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
