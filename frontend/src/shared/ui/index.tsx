import type { ReactNode } from "react";

/**
 * 设计系统原语 —— 全站视觉词汇的唯一来源。
 *
 * 只收「到处都在用」的元素（卡片/按钮/标签/空态/分栏）；一次性样式留在
 * feature 里。这样改视觉只需改这里，不必全仓搜 class。
 */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-line bg-surface shadow-sm ${className}`}>
      {children}
    </div>
  );
}

/**
 * 输入控件的外观（`input` / `textarea` 共用）。
 *
 * 抽出来是因为这段 class 曾在 **7 处逐字复制**（岗位搜索框、画像三行、配置页三行）——
 * 「输入框描边 + 内边距」这个语义就有 7 份定义，换一次样式得全仓找。这里只放
 * **外观**；宽度、等宽字体、行数这些各处不同的部分由调用方追加。
 *
 * `bg-surface` 不能少：Tailwind v4 的 preflight 把表单控件设成
 * `background-color: transparent`，在页面底色的 `canvas`（浅灰）上会露出灰底 ——
 * 抽公共类时漏掉它，搜索框就从白底变成灰底（这次实打实踩到了）。
 */
export const fieldClass = "rounded border border-field bg-surface px-3 py-2";

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "default";
  disabled?: boolean;
  className?: string;
  /** `aria-describedby` —— 表格行内的按钮靠它补上下文（例如念出岗位名）。 */
  describedBy?: string;
};

export function Button({
  children,
  onClick,
  type = "button",
  variant = "default",
  disabled = false,
  className = "",
  describedBy,
}: ButtonProps) {
  const base =
    "rounded px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const look =
    variant === "primary"
      ? "bg-ink text-white hover:bg-ink-soft"
      : "border border-field bg-surface hover:bg-surface-sunken";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      aria-describedby={describedBy}
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
    neutral: "bg-surface-sunken text-ink-soft",
    good: "bg-success-soft text-success",
    warn: "bg-warning-soft text-warning",
    bad: "bg-danger-soft text-danger",
  } as const;
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs ${tones[tone]}`}>{children}</span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-field p-8 text-center text-muted">
      {children}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <p className="rounded border border-danger-line bg-danger-soft p-3 text-sm text-danger">{message}</p>
  );
}

export function SuccessNotice({ message }: { message: string }) {
  return <p className="text-sm text-success">{message}</p>;
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
        {subtitle != null && <div className="text-sm text-muted">{subtitle}</div>}
      </div>
      {actions}
    </header>
  );
}

/**
 * 分页控件（纯展示）。首页且无下一页时不渲染。
 *
 * 住在 `shared/ui` 而不是某个 feature 目录里：岗位库与会话侧栏都要用，
 * 同一语义只该有一处定义。原先它内联在 `features/jobs/components/` 下，
 * 别的 feature 要用就只能深链别人的领地（AGENTS.md 的切片所有权）。
 *
 * `ariaLabel` 是**必填**：页内可能同时存在多个 `<nav>`，没有可访问名时
 * 屏幕阅读器的地标列表里会出现两个无法区分的「navigation」。
 * `className` 供调用方补布局（如窄侧栏里 `flex-wrap`），视觉词汇仍由这里定。
 */
export function Pager({
  page,
  hasNext,
  onPageChange,
  ariaLabel,
  className = "",
}: {
  page: number;
  hasNext: boolean;
  onPageChange: (page: number) => void;
  ariaLabel: string;
  className?: string;
}) {
  if (page <= 1 && !hasNext) {
    return null;
  }
  return (
    <nav
      aria-label={ariaLabel}
      className={`mt-4 flex items-center gap-3 text-sm ${className}`}
    >
      <Button disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        ← 上一页
      </Button>
      <span className="text-muted">第 {page} 页</span>
      <Button disabled={!hasNext} onClick={() => onPageChange(page + 1)}>
        下一页 →
      </Button>
    </nav>
  );
}
