import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationMessageView = components["schemas"]["ConversationMessageView"];
/** 会话列表页 —— **分页**的（`page` / `page_size` / `has_next`，见后端 `MAX_PAGE_SIZE`）。 */
export type ConversationList = components["schemas"]["ConversationListResponse"];

/** 一页取多少个会话（后端上限也是 50）。 */
export const CONVERSATIONS_PAGE_SIZE = 50;

export async function fetchConversations(page: number): Promise<ConversationList> {
  const { data, error, response } = await api.GET("/api/assistant/conversations", {
    params: { query: { page, page_size: CONVERSATIONS_PAGE_SIZE } },
  });
  if (error || !data) {
    // 用共享的错误漏斗取可读原因（后端 5xx 的 detail 由它按规则决定是否透出），
    // 不再手写固定文案把原因整段吞掉。
    throw new Error(apiErrorMessage(error, "加载会话失败", response));
  }
  return data;
}

export function useConversations(page: number) {
  return useQuery({
    queryKey: ["assistant", "conversations", page],
    queryFn: () => fetchConversations(page),
    // 翻页时保留上一页内容，避免侧栏闪白（与岗位库同一取舍）。
    placeholderData: (previous) => previous,
  });
}

export async function fetchMessages(conversationId: string, signal?: AbortSignal): Promise<ConversationMessageView[]> {
  const { data, error, response } = await api.GET("/api/assistant/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } },
    signal,
  });
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载会话消息失败", response));
  }
  return data;
}

export function useConversationMessages(conversationId: string | null) {
  return useQuery({
    queryKey: ["assistant", "messages", conversationId],
    queryFn: ({ signal }) => fetchMessages(conversationId as string, signal),
    enabled: conversationId !== null,
  });
}

/** 发送后让会话列表与消息失效（新一轮已落库）。 */
export function useRefreshConversations() {
  const queryClient = useQueryClient();
  return async (conversationId: string) => {
    // 首次历史请求可能在 done 前已开始且还没返回；先取消，避免它的旧快照
    // 被当成本轮完成后的刷新。新会话的请求会在选中 id 后自然启动。
    await queryClient.cancelQueries({ queryKey: ["assistant", "messages", conversationId] });
    await queryClient.invalidateQueries({ queryKey: ["assistant"] });
  };
}
