import { useEffect, useRef, useState } from "react";

import { Button, Card, EmptyState, ErrorNotice, PageHeader } from "../../shared/ui";
import { apiUrl } from "../../shared/api/client";
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

  // 卸载时中止在飞的流：否则回调会继续对已卸载的组件 setState
  // （切走路由后流还在跑，白耗流量也白改状态）。
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

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
        apiUrl("/api/assistant/stream"),
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
            // 关键顺序：先清 live，再 refresh。
            //
            // 后端在 yield `done` **之前**就 `_persist` 落库了（见 router.py 的
            // `_stream_turn`），所以 refresh 拉回来的 history 已经包含本轮两条消息。
            // 若不先清 live，`items = [...history, ...live]` 会把本轮显示两遍。
            //
            // 代价：history 重取完成前会短暂空一瞬。本工具是本地 SQLite，
            // 重取是毫秒级；而「消息重复显示」是确定性错误 —— 两害相权取此。
            setLive([]);
            refresh();
          } else if (event.type === "error") {
            setError(String(event.message ?? "未知错误"));
          }
        },
        controller.signal,
      );
    } catch (exc) {
      // 用户主动「中止」不是错误 —— 别把 AbortError 当失败弹给用户
      const aborted = exc instanceof DOMException && exc.name === "AbortError";
      if (!aborted) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
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
    // 窄屏堆叠（会话列表在上、对话区在下），宽屏恢复左侧栏。
    // 224px 的固定侧栏在 390px 窗口里会吃掉近六成宽度。
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      <aside className="md:w-56 md:shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-700">会话</h2>
          <Button
            disabled={streaming}
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
              {/* 流式进行中禁止切换：切走会把本轮流内容追加到另一个会话上，
                  且 done 后 currentId 被覆盖回去 —— 用户的选择被静默撤销。 */}
              <button
                type="button"
                disabled={streaming}
                className={`w-full truncate rounded px-2 py-1 text-left text-sm ${
                  currentId === item.id ? "bg-slate-900 text-white" : "hover:bg-slate-100"
                } ${streaming ? "cursor-not-allowed opacity-50" : ""}`}
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
                // 消息模型没有 id，用「role + 内容前缀 + 序号」组合做 key：
                // 列表只追加、不重排，这个组合在其中是稳定的；纯 index 在将来若
                // 支持重排/删除时会复用错 keyed 状态。
                <MessageBubble key={`${item.role}:${item.content.slice(0, 32)}:${index}`} item={item} />
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
