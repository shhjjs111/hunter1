import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiUrl } from "../../shared/api/client";
import { streamSse } from "../../shared/streaming/sse";
import { useConversationMessages, useConversations, useRefreshConversations } from "./api";
import type { ChatItem } from "./components/MessageBubble";

/**
 * 求职助手的会话状态机：历史 + 本轮流式消息 + 落库判定。
 *
 * 桌面页与手机页共用这一份实现 —— 两边只有布局不同，流式、中止、失败回填、
 * 截断提示这些行为必须完全一致。
 */
export function useAssistantChat() {
  const queryClient = useQueryClient();
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const conversations = useConversations(page);
  const messages = useConversationMessages(currentId);
  const refresh = useRefreshConversations();

  const [input, setInput] = useState("");
  const [live, setLive] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [settledTurn, setSettledTurn] = useState<{ id: string; afterUpdate: number } | null>(null);
  const sawAssistantText = useRef(false);
  const assistantChunks = useRef<string[]>([]);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const history: ChatItem[] = (messages.data ?? []).map((item) => ({
    role: item.role,
    content: item.content,
  }));
  // 历史接口有窗口上限，同一句提问的数量不一定增加。done 之后成功重取的历史
  // 才能替代 live；按缓存更新序号判定，也不会把同一毫秒内的刷新漏掉。
  const historyUpdates = queryClient.getQueryState(["assistant", "messages", currentId])?.dataUpdateCount ?? 0;
  const turnPersisted = settledTurn?.id === currentId &&
    historyUpdates > settledTurn.afterUpdate;
  const items = currentId === null ? live : [...history, ...(turnPersisted ? [] : live)];
  const historyLoading = currentId !== null && messages.isLoading;
  const historyError = currentId !== null && messages.isError;

  function appendAssistantText(chunk: string) {
    if (chunk.trim() !== "") {
      sawAssistantText.current = true;
    }
    assistantChunks.current.push(chunk);
    const content = assistantChunks.current.join("");
    setLive((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === "assistant") {
        return [...prev.slice(0, -1), { ...last, content }];
      }
      return [...prev, { role: "assistant", content }];
    });
  }

  function resetTurn(): void {
    setSettledTurn(null);
  }

  function clearConversationState(): void {
    setLive([]);
    resetTurn();
    setError(null);
    setNotice(null);
  }

  function newConversation(): void {
    if (streaming) {
      return;
    }
    setCurrentId(null);
    clearConversationState();
  }

  function openConversation(id: string): void {
    if (streaming) {
      return;
    }
    setCurrentId(id);
    clearConversationState();
  }

  async function send(prompt?: string) {
    const text = (prompt ?? input).trim();
    if (!text || streaming) {
      return;
    }
    setInput("");
    setError(null);
    setNotice(null);
    setLive([{ role: "user", content: text }]);
    sawAssistantText.current = false;
    assistantChunks.current = [];
    setSettledTurn(null);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let finished = false;
    let failed = false;
    let dropped = false;
    let truncatedNotice = false;

    try {
      await streamSse(
        apiUrl("/api/assistant/stream"),
        { message: text, conversation_id: currentId ?? "" },
        (event) => {
          if (!alive.current) {
            return;
          }
          if (event.type === "text") {
            appendAssistantText(String(event.text ?? ""));
          } else if (event.type === "tool_start") {
            assistantChunks.current = [];
            setLive((prev) => [
              ...prev,
              { role: "tool", content: `正在调用 ${String(event.name)}…` },
            ]);
          } else if (event.type === "tool_end") {
            assistantChunks.current = [];
            const ok = event.ok === true;
            setLive((prev) => [
              ...prev,
              {
                role: "tool",
                content: ok
                  ? String(event.content ?? "").slice(0, 300)
                  : `工具失败：${String(event.error ?? "")}`,
              },
            ]);
          } else if (event.type === "done") {
            finished = true;
            const id = String(event.conversation_id ?? "");
            if (id) {
              setSettledTurn({
                id,
                afterUpdate: queryClient.getQueryState(["assistant", "messages", id])?.dataUpdateCount ?? 0,
              });
              setCurrentId(id);
              setPage(1);
            }
            if (typeof event.reply === "string" && event.reply.trim() !== "") {
              assistantChunks.current = [];
              appendAssistantText(event.reply);
            }
            const truncated = event.truncated === true;
            const degraded = event.degraded === true;
            truncatedNotice = truncated || degraded;
            setNotice(
              truncated
                ? "回答达到轮次上限被截断，内容可能不完整。"
                : degraded
                  ? "本次回答由模型降级返回（可能不完整）。"
                  : null,
            );
            void refresh(id);
          } else if (event.type === "error") {
            failed = true;
            setError(String(event.message ?? "未知错误"));
          }
        },
        controller.signal,
        () => {
          dropped = true;
        },
      );
    } catch (exc) {
      const aborted =
        controller.signal.aborted ||
        (exc instanceof DOMException && exc.name === "AbortError");
      if (!aborted) {
        failed = true;
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    } finally {
      if (alive.current) {
        setStreaming(false);
      }
      abortRef.current = null;
    }

    if (!alive.current) {
      return;
    }

    if (controller.signal.aborted && !finished) {
      if (sawAssistantText.current) {
        setNotice("已中止：这段回答没有保存，刷新或切换会话后不会保留。");
      } else {
        setNotice("已中止：本轮还没有产生回答，内容未保存。");
      }
    } else if (!finished && !failed) {
      setNotice("连接中断：回答可能不完整，且本轮没有保存。");
    } else if (dropped && !truncatedNotice) {
      setNotice("连接异常：有内容未能解析，这段回答可能不完整。");
    }

    if (!finished) {
      setInput((current) => (current === "" ? text : current));
    }
  }

  return {
    currentId,
    page,
    setPage,
    conversations,
    messages,
    input,
    setInput,
    streaming,
    error,
    notice,
    items,
    historyLoading,
    historyError,
    send,
    abort: () => abortRef.current?.abort(),
    newConversation,
    openConversation,
  };
}
