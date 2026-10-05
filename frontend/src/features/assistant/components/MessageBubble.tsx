export type ChatItem = {
  role: string;
  content: string;
  /** 流式追加用的标记：同一段回答尚未封口时继续往里追加。 */
  sealed?: boolean;
};

const ROLE_STYLE: Record<string, string> = {
  user: "bg-slate-900 text-white",
  assistant: "bg-white border border-slate-200",
  tool: "bg-amber-50 border border-amber-200 text-amber-900 text-xs",
};

const ROLE_LABEL: Record<string, string> = {
  user: "你",
  assistant: "助手",
  tool: "工具",
  system: "系统",
};

/** 一条对话气泡（纯展示）。 */
export function MessageBubble({ item }: { item: ChatItem }) {
  const style = ROLE_STYLE[item.role] ?? "bg-slate-100";
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
