import { Button, Card, EmptyState, ErrorNotice, PageHeader, Pager, fieldClass } from "../../shared/ui";
import { MessageBubble } from "./components/MessageBubble";
import { useAssistantChat } from "./useAssistantChat";

export function AssistantPage() {
  const chat = useAssistantChat();
  const { conversations, messages, items, historyLoading, historyError } = chat;

  return (
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      <aside className="md:w-56 md:shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-ink-soft">会话</h2>
          <Button disabled={chat.streaming} onClick={chat.newConversation}>
            新对话
          </Button>
        </div>
        {conversations.isLoading && <p className="px-2 text-sm text-muted">加载会话列表…</p>}
        {conversations.isError && (
          <div className="px-2">
            <ErrorNotice message={(conversations.error as Error).message} />
          </div>
        )}
        <ul className="space-y-1">
          {(conversations.data?.items ?? []).map((item) => (
            <li key={item.id}>
              <button
                type="button"
                disabled={chat.streaming}
                className={`w-full truncate rounded-full px-3.5 py-2 text-left text-sm transition-colors ${
                  chat.currentId === item.id
                    ? "bg-fill-active text-ink pill-shadow"
                    : "text-muted-strong hover:bg-fill"
                } ${chat.streaming ? "cursor-not-allowed opacity-50" : ""}`}
                onClick={() => chat.openConversation(item.id)}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>
        {conversations.data && (conversations.data.has_next || chat.page > 1) && (
          <div className="mt-2 px-2">
            <p className="text-xs text-muted">共 {conversations.data.total} 个会话</p>
            <Pager
              ariaLabel="会话分页"
              className="mt-1 flex-wrap"
              page={chat.page}
              hasNext={conversations.data.has_next}
              busy={conversations.isPlaceholderData}
              onPageChange={chat.setPage}
            />
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="求职助手" subtitle="只读：它查岗位与投递、给建议；写操作由你确认" />

        <Card className="max-h-[60vh] overflow-y-auto p-4">
          {historyError && (
            <div className="mb-3">
              <ErrorNotice message={`加载会话历史失败：${(messages.error as Error).message}`} />
            </div>
          )}
          {historyLoading && <p className="mb-3 text-sm text-muted">加载会话历史…</p>}
          {items.length > 0 && (
            <div className="space-y-3" role="log" aria-live="polite" aria-busy={chat.streaming}>
              {items.map((item, index) => (
                <MessageBubble key={`${item.role}:${index}`} item={item} />
              ))}
            </div>
          )}
          {items.length === 0 && !historyLoading && !historyError && (
            <EmptyState>问点什么吧，比如「有哪些产品经理的岗位？」</EmptyState>
          )}
        </Card>

        {chat.error !== null && (
          <div className="mt-3">
            <ErrorNotice message={chat.error} />
          </div>
        )}

        {chat.notice !== null && chat.error === null && (
          <p className="mt-3 text-sm text-warning">{chat.notice}</p>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void chat.send();
          }}
        >
          <input
            className={`flex-1 ${fieldClass}`}
            placeholder="有什么想问的？"
            aria-label="输入要问助手的问题"
            value={chat.input}
            onChange={(event) => chat.setInput(event.target.value)}
            disabled={chat.streaming}
          />
          <Button type="submit" variant="primary" disabled={chat.streaming || chat.input.trim() === ""}>
            {chat.streaming ? "回复中…" : "发送"}
          </Button>
          {chat.streaming && <Button onClick={chat.abort}>中止</Button>}
        </form>
      </div>
    </div>
  );
}
