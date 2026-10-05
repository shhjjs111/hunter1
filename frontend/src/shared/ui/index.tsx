import type { ReactNode } from "react";

/**
 * 设计系统原语 —— 全站视觉词汇的唯一来源。
 *
 * 只收「到处都在用」的元素（卡片/按钮/标签/空态/分栏）；一次性样式留在
 * feature 里。这样改视觉只需改这里，不必全仓搜 class。
 */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {children}
    </div>
  );
}

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "default";
  disabled?: boolean;
  className?: string;
};

export function Button({
  children,
  onClick,
  type = "button",
  variant = "default",
  disabled = false,
  className = "",
}: ButtonProps) {
  const base =
    "rounded px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const look =
    variant === "primary"
      ? "bg-slate-900 text-white hover:bg-slate-700"
      : "border border-slate-300 bg-white hover:bg-slate-100";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${look} ${className}`}
    >
      {children}
    </button>
  );
}

export function Tag({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const tones = {
    neutral: "bg-slate-100 text-slate-700",
    good: "bg-emerald-50 text-emerald-700",
    warn: "bg-amber-50 text-amber-700",
    bad: "bg-red-50 text-red-700",
  } as const;
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-slate-500">
      {children}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{message}</p>
  );
}

export function SuccessNotice({ message }: { message: string }) {
  return <p className="text-sm text-emerald-600">{message}</p>;
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex items-baseline justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle != null && <div className="text-sm text-slate-500">{subtitle}</div>}
      </div>
      {actions}
    </header>
  );
}
