export type ChatItem = {
  role: string;
  content: string;
  /** 流式追加用的标记：同一段回答尚未封口时继续往里追加。 */
  sealed?: boolean;
};

const ROLE_STYLE: Record<string, string> = {
  user: "bg-ink text-white",
  assistant: "bg-surface border border-line",
  tool: "bg-warning-soft border border-warning-line text-warning-strong text-xs",
};

const ROLE_LABEL: Record<string, string> = {
  user: "你",
  assistant: "助手",
  tool: "工具",
  system: "系统",
};

/** 一条对话气泡（纯展示）。 */
export function MessageBubble({ item }: { item: ChatItem }) {
  const style = ROLE_STYLE[item.role] ?? "bg-surface-sunken";
  const isUser = item.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${style}`}>
        <div className="mb-0.5 text-xs opacity-60">{ROLE_LABEL[item.role] ?? item.role}</div>
        <div className="whitespace-pre-wrap break-words">{item.content}</div>
      </div>
    </div>
  );
}
