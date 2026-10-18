import { useEffect, type ReactNode } from "react";

/**
 * 设计系统原语 —— 全站视觉词汇的唯一来源。
 *
 * 只收「到处都在用」的元素（卡片/按钮/标签/空态/分栏）；一次性样式留在
 * feature 里。这样改视觉只需改这里，不必全仓搜 class。
 */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`glass-thin rounded-3xl ${className}`}>
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
export const fieldClass =
  "recessed rounded-2xl px-4 py-2.5 text-ink placeholder:text-subtle transition-shadow focus:shadow-[inset_0_0_0_1px_rgb(255_255_255/0.35)] focus:outline-none";

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
    "lift rounded-full px-4 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40";
  const look =
    variant === "primary"
      ? "bg-accent text-on-accent shadow-[0_6px_20px_-8px_var(--color-accent)] hover:bg-accent-hover"
      : "bg-fill text-ink shadow-[inset_0_1px_0_var(--glass-spec),inset_0_0_0_1px_var(--btn-edge)] hover:bg-fill-strong";
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
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="glass-thin rounded-3xl p-10 text-center text-muted">
      {children}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <p
      role="alert"
      aria-live="assertive"
      className="rounded-2xl border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger"
    >
      {message}
    </p>
  );
}

export function SuccessNotice({ message }: { message: string }) {
  return (
    <p role="status" aria-live="polite" className="text-sm text-success">
      {message}
    </p>
  );
}

/**
 * 页面级操作反馈。
 *
 * 行内操作（岗位库的「评分 / 记录投递」）发生在表格里，而结果提示若插在
 * 表格与分页器之后，桌面首屏和手机视口都可能完全看不到 —— 用户点完按钮会以为
 * 什么都没发生。这里固定在视口底部、dock 上方，反馈不随内容位置漂走。
 *
 * 成功提示 4 秒后自动收起；错误必须由用户确认，否则失败原因会在用户还没读完时
 * 消失。`onDismiss` 由调用方提供，避免组件自己持有与 mutation 状态脱节的可见性。
 */
export function Toast({
  tone,
  message,
  onDismiss,
}: {
  tone: "success" | "error";
  message: string;
  onDismiss?: () => void;
}) {
  useEffect(() => {
    if (tone !== "success" || onDismiss === undefined) {
      return;
    }
    const timer = window.setTimeout(onDismiss, 4000);
    return () => window.clearTimeout(timer);
  }, [tone, message, onDismiss]);

  const isError = tone === "error";
  return (
    <div
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      aria-atomic="true"
      className={`glass fixed bottom-[84px] left-1/2 z-40 flex w-[calc(100%-2rem)] max-w-md -translate-x-1/2 items-start gap-3 rounded-full px-4 py-3 text-sm shadow-[0_18px_50px_-18px_rgb(0_0_0/0.55)] md:bottom-[104px] ${
        isError ? "text-danger" : "text-success"
      }`}
    >
      <span className="min-w-0 flex-1">{message}</span>
      {isError && onDismiss !== undefined && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭提示"
          className="grid size-6 shrink-0 place-items-center rounded-full bg-fill text-ink-soft transition-colors hover:bg-fill-strong hover:text-ink"
        >
          ×
        </button>
      )}
    </div>
  );
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
    <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[2rem] leading-tight font-semibold">{title}</h1>
        {subtitle != null && <div className="mt-1 text-sm text-muted">{subtitle}</div>}
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
 *
 * `busy`：调用方正在取「当前页」的数据（react-query 的 `isPlaceholderData`）。
 * 这时屏幕上的 `hasNext` 还是**上一页**的 —— 不置忙的话「下一页」照旧可点，
 * 连点两下就跳过一整页、直接落到末页之后（看到一页空白）。
 */
export function Pager({
  page,
  hasNext,
  onPageChange,
  ariaLabel,
  className = "",
  busy = false,
}: {
  page: number;
  hasNext: boolean;
  onPageChange: (page: number) => void;
  ariaLabel: string;
  className?: string;
  busy?: boolean;
}) {
  if (page <= 1 && !hasNext) {
    return null;
  }
  return (
    <nav
      aria-label={ariaLabel}
      className={`mt-4 flex items-center gap-3 text-sm ${className}`}
    >
      <Button disabled={busy || page <= 1} onClick={() => onPageChange(page - 1)}>
        ← 上一页
      </Button>
      <span className="text-muted">第 {page} 页</span>
      <Button disabled={busy || !hasNext} onClick={() => onPageChange(page + 1)}>
        下一页 →
      </Button>
    </nav>
  );
}
