import { useRef, useState } from "react";

import { Button, Card, EmptyState, ErrorNotice, PageHeader } from "../../shared/ui";
import { streamSse } from "../../shared/streaming/sse";
import { useConversationMessages, useConversations, useRefreshConversations } from "./api";
import { MessageBubble, type ChatItem } from "./components/MessageBubble";

export function AssistantPage() {
  const conversations = useConversations();
  const [currentId, setCurrentId] = useState<string | null>(null);
  const messages = useConversationMessages(currentId);
  const refresh = useRefreshConversations();

  const [input, setInput] = useState("");
  const [live, setLive] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 历史消息（已落库的）与本次流式（还在飞的）拼在一起展示
  const history: ChatItem[] = (messages.data ?? []).map((item) => ({
    role: item.role,
    content: item.content,
  }));
  const items = currentId === null ? live : [...history, ...live];

  async function send() {
    const text = input.trim();
    if (!text || streaming) {
      return;
    }
    setInput("");
    setError(null);
    setLive([{ role: "user", content: text }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamSse(
        "/api/assistant/stream",
        { message: text, conversation_id: currentId ?? "" },
        (event) => {
          if (event.type === "text") {
            appendAssistantText(String(event.text ?? ""));
          } else if (event.type === "tool_start") {
            setLive((prev) => [
              ...prev,
              { role: "tool", content: `正在调用 ${String(event.name)}…` },
            ]);
          } else if (event.type === "tool_end") {
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
            const id = String(event.conversation_id ?? "");
            if (id) {
              setCurrentId(id);
            }
            refresh();
          } else if (event.type === "error") {
            setError(String(event.message ?? "未知错误"));
          }
        },
        controller.signal,
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function appendAssistantText(chunk: string) {
    setLive((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === "assistant" && !last.sealed) {
        return [...prev.slice(0, -1), { ...last, content: last.content + chunk }];
      }
      return [...prev, { role: "assistant", content: chunk }];
    });
  }

  return (
    <div className="flex gap-6">
      <aside className="w-56 shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-700">会话</h2>
          <Button
            onClick={() => {
              setCurrentId(null);
              setLive([]);
              setError(null);
            }}
          >
            新对话
          </Button>
        </div>
        <ul className="space-y-1">
          {(conversations.data ?? []).map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={`w-full truncate rounded px-2 py-1 text-left text-sm ${
                  currentId === item.id ? "bg-slate-900 text-white" : "hover:bg-slate-100"
                }`}
                onClick={() => {
                  setCurrentId(item.id);
                  setLive([]);
                  setError(null);
                }}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="求职助手" subtitle="只读：它查岗位与投递、给建议；写操作由你确认" />

        <Card className="flex-1 overflow-y-auto p-4" >
          {items.length === 0 ? (
            <EmptyState>问点什么吧，比如「有哪些产品经理的岗位？」</EmptyState>
          ) : (
            <div className="space-y-3">
              {items.map((item, index) => (
                <MessageBubble key={index} item={item} />
              ))}
            </div>
          )}
        </Card>

        {error !== null && (
          <div className="mt-3">
            <ErrorNotice message={error} />
          </div>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2"
            placeholder="有什么想问的？"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={streaming}
          />
          <Button type="submit" variant="primary" disabled={streaming || input.trim() === ""}>
            {streaming ? "回复中…" : "发送"}
          </Button>
          {streaming && <Button onClick={() => abortRef.current?.abort()}>中止</Button>}
        </form>
      </div>
    </div>
  );
}
