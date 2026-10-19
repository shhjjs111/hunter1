import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useMobileChrome } from "../../app/mobileChrome";
import { MobileIcon, useDialogLifecycle } from "../../shared/ui/mobile";
import { useAssistantChat } from "./useAssistantChat";

const SUGGESTIONS = ["哪些岗位还没评分？", "我投的进展到哪了？", "帮我准备二面"];

export function MobileAssistantPage() {
  const { drawerOpen, setDrawerOpen, setKeyboardOpen, setOverlayOpen } = useMobileChrome();
  const chat = useAssistantChat();
  const [keyboardInset, setKeyboardInset] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  const closeDrawer = useCallback(() => setDrawerOpen(false), [setDrawerOpen]);
  useDialogLifecycle(drawerOpen, closeDrawer, asideRef);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) {
      return;
    }
    const onResize = () => {
      const keyboard = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
      const open = keyboard > 120;
      setKeyboardInset(open ? keyboard : 0);
      setKeyboardOpen(open);
      if (open) {
        window.requestAnimationFrame(() => {
          inputRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
        });
      }
    };
    viewport.addEventListener("resize", onResize);
    return () => {
      viewport.removeEventListener("resize", onResize);
      setKeyboardOpen(false);
    };
  }, [setKeyboardOpen]);

  useEffect(() => {
    setOverlayOpen(drawerOpen);
    return () => setOverlayOpen(false);
  }, [drawerOpen, setOverlayOpen]);

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDrawerOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen, setDrawerOpen]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) {
      node.scrollTo?.({ top: node.scrollHeight, behavior: "smooth" });
    }
  }, [chat.items]);

  const conversations = chat.conversations;

  return (
    <div className="mobile-assistant-page">
      <p className="mobile-readonly">只读：它查岗位与投递、给建议；写操作由你确认</p>

      <div className="mobile-chat" ref={scrollRef} role="log" aria-live="polite" aria-busy={chat.streaming}>
        <div className="mobile-messages">
          {chat.historyError && (
            <p className="mobile-error">加载会话历史失败：{(chat.messages.error as Error).message}</p>
          )}
          {chat.historyLoading && <p className="mobile-subtitle">加载会话历史…</p>}
          {chat.items.map((item, index) => {
            const isLast = index === chat.items.length - 1;
            return (
              <div
                key={`${item.role}:${index}`}
                className={`mobile-bubble ${item.role === "user" ? "user" : item.role === "tool" ? "tool" : "assistant"}`}
              >
                {item.role === "tool" ? `✦ ${item.content}` : item.content}
                {chat.streaming && isLast && item.role === "assistant" && (
                  <span className="mobile-caret" aria-hidden="true" />
                )}
              </div>
            );
          })}
          {chat.items.length === 0 && !chat.historyLoading && !chat.historyError && (
            <div className="mobile-bubble assistant">
              你好，我能查你的岗位库和投递记录，帮你准备面试、改简历、比 offer。想从哪开始？
            </div>
          )}
        </div>

        {chat.items.length === 0 && !chat.streaming && (
          <div className="mobile-suggestions">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="mobile-chip"
                onClick={() => void chat.send(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}

        {chat.error && <p className="mobile-error">{chat.error}</p>}
        {!chat.error && chat.notice && <p className="text-sm text-warning">{chat.notice}</p>}

        <div className="mobile-composer-wrap" style={keyboardInset ? { bottom: keyboardInset } : undefined}>
          <form
            className="mobile-composer glass mobile-no-swipe"
            onSubmit={(event) => {
              event.preventDefault();
              if (chat.streaming) {
                chat.abort();
              } else {
                void chat.send();
              }
            }}
          >
            <input
              ref={inputRef}
              value={chat.input}
              onChange={(event) => chat.setInput(event.target.value)}
              onFocus={() => setKeyboardOpen(true)}
              onBlur={() => setKeyboardOpen(false)}
              disabled={chat.streaming}
              placeholder="有什么想问的？"
              aria-label="输入要问助手的问题"
              autoComplete="off"
            />
            <button
              type={chat.streaming ? "button" : "submit"}
              className="mobile-send"
              disabled={!chat.streaming && chat.input.trim() === ""}
              aria-label={chat.streaming ? "中止回答" : "发送"}
              onClick={chat.streaming ? chat.abort : undefined}
            >
              <MobileIcon name={chat.streaming ? "stop" : "send"} className="size-[18px]" />
            </button>
          </form>
        </div>
      </div>

      {drawerOpen &&
        createPortal(
          <div className="mobile-overlay">
            <button
              type="button"
              aria-label="关闭会话列表"
              className="mobile-overlay-backdrop"
              onClick={closeDrawer}
            />
            <aside
              ref={asideRef}
              className="mobile-drawer"
              role="dialog"
              aria-modal="true"
              aria-label="会话列表"
              tabIndex={-1}
            >
              <div className="flex items-center justify-between gap-2 px-1.5">
                <h2>会话</h2>
                <button
                  type="button"
                  className="mobile-icon-button glass"
                  aria-label="新对话"
                  disabled={chat.streaming}
                  onClick={() => {
                    chat.newConversation();
                    closeDrawer();
                  }}
                >
                  <MobileIcon name="plus" className="size-[18px]" />
                </button>
              </div>
              {conversations.isLoading && <p className="text-sm text-muted">加载会话列表…</p>}
              {conversations.isError && (
                <p className="mobile-error">{(conversations.error as Error).message}</p>
              )}
              {(conversations.data?.items ?? []).map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  className={`mobile-conversation ${chat.currentId === conversation.id ? "on" : ""}`}
                  disabled={chat.streaming}
                  onClick={() => {
                    chat.openConversation(conversation.id);
                    closeDrawer();
                  }}
                >
                  {conversation.title}
                  <small>{formatDay(conversation.updated_at)}</small>
                </button>
              ))}
              {conversations.data && conversations.data.items.length === 0 && (
                <p className="px-2 text-sm text-muted">还没有会话。点右上角 + 开始新的对话。</p>
              )}
              {conversations.data && (conversations.data.has_next || chat.page > 1) && (
                <div className="mt-3 flex items-center justify-between gap-2 px-2">
                  <button
                    type="button"
                    className="mobile-button"
                    disabled={chat.page <= 1}
                    onClick={() => chat.setPage(chat.page - 1)}
                  >
                    上一页
                  </button>
                  <span className="text-xs text-muted">
                    {chat.page} / {Math.max(1, Math.ceil(conversations.data.total / conversations.data.page_size))}
                  </span>
                  <button
                    type="button"
                    className="mobile-button"
                    disabled={!conversations.data.has_next}
                    onClick={() => chat.setPage(chat.page + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
            </aside>
          </div>,
          document.body,
        )}
    </div>
  );
}

function formatDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
