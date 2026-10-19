import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type MobileIconName =
  | "search"
  | "menu"
  | "plus"
  | "chevron"
  | "check"
  | "eye"
  | "eyeOff"
  | "send"
  | "stop"
  | "close"
  | "external"
  | "sun"
  | "moon";

/** 手机端小图标（1.75 描边），纯装饰。 */
export function MobileIcon({ name, className = "size-5" }: { name: MobileIconName; className?: string }) {
  const paths: Record<MobileIconName, ReactNode> = {
    search: (
      <>
        <circle cx="10.8" cy="10.8" r="6.8" />
        <path d="m16 16 4.2 4.2" />
      </>
    ),
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    plus: <path d="M12 5v14M5 12h14" />,
    chevron: <path d="m8 10 4 4 4-4" />,
    check: <path d="m5 12.5 4.2 4.2L19 7" />,
    eye: (
      <>
        <path d="M2.8 12s3.4-5.5 9.2-5.5 9.2 5.5 9.2 5.5-3.4 5.5-9.2 5.5S2.8 12 2.8 12Z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    eyeOff: (
      <>
        <path d="M4 4.5 20 20" />
        <path d="M9.4 6.8A10.6 10.6 0 0 1 12 6.5c5.8 0 9.2 5.5 9.2 5.5a16 16 0 0 1-3.1 3.5M6.2 8.3A15.1 15.1 0 0 0 2.8 12s3.4 5.5 9.2 5.5c1.3 0 2.5-.3 3.5-.7" />
      </>
    ),
    send: <path d="M12 19V5m0 0-5.5 5.5M12 5l5.5 5.5" />,
    stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    external: (
      <>
        <path d="M14 4h6v6M20 4l-8.5 8.5" />
        <path d="M18.5 13.5V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V7A1.5 1.5 0 0 1 5 5.5h5.5" />
      </>
    ),
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2.5v2M12 19.5v2M21.5 12h-2M4.5 12h-2M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4M18.7 18.7l-1.4-1.4M6.7 6.7 5.3 5.3" />
      </>
    ),
    moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />,
  };
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

export function useDialogLifecycle(
  open: boolean,
  onClose: () => void,
  ref: React.RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const previous = document.activeElement as HTMLElement | null;
    const frame = window.requestAnimationFrame(() => ref.current?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !ref.current) {
        return;
      }
      const focusable = Array.from(
        ref.current.querySelectorAll<HTMLElement>(
          'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element.offsetParent !== null || element === document.activeElement);
      if (focusable.length === 0) {
        event.preventDefault();
        ref.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose, open, ref]);
}

/**
 * 底部 Sheet：80% 高、玻璃材质、拖动把手；下拉超过 130px（或快速甩下）关闭。
 */
export function Sheet({
  open,
  onClose,
  children,
  footer,
  label = "详情",
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ y: number; time: number; dy: number } | null>(null);
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  useDialogLifecycle(open, onClose, ref);

  if (!open) {
    return null;
  }

  const start = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.target instanceof Element && event.target.closest("button,input,textarea,select,a")) {
      return;
    }
    const body = ref.current?.querySelector<HTMLElement>(".mobile-sheet-body");
    if (body && body.scrollTop > 0) {
      return;
    }
    dragRef.current = { y: event.clientY, time: performance.now(), dy: 0 };
    setDragging(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) {
      return;
    }
    let dy = event.clientY - dragRef.current.y;
    if (dy < 0) {
      dy *= 0.2;
    }
    dragRef.current.dy = dy;
    setDragY(dy);
  };
  const end = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (!drag) {
      return;
    }
    const velocity = drag.dy / Math.max(1, performance.now() - drag.time);
    if (drag.dy > 130 || (velocity > 0.6 && drag.dy > 30)) {
      setDragY(0);
      onClose();
      return;
    }
    setDragY(0);
  };

  return createPortal(
    <div className="mobile-overlay">
      <button type="button" aria-label="关闭弹层" className="mobile-overlay-backdrop" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={`mobile-sheet glass ${dragging ? "dragging" : ""}`}
        style={dragY ? { transform: `translateY(${dragY}px)`, transition: "none" } : undefined}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
      >
        <div className="mobile-grab" aria-hidden="true" />
        <div className="mobile-sheet-body">{children}</div>
        {footer && <div className="mobile-sheet-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export type ActionSheetOption = {
  label: string;
  value: string;
  destructive?: boolean;
  checked?: boolean;
};

/** iOS 风格 Action Sheet：一组选项 + 独立的取消按钮。 */
export function ActionSheet({
  open,
  title,
  options,
  onSelect,
  onCancel,
}: {
  open: boolean;
  title?: string;
  options: ActionSheetOption[];
  onSelect: (value: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useDialogLifecycle(open, onCancel, ref);
  if (!open) {
    return null;
  }
  return createPortal(
    <div className="mobile-overlay">
      <button type="button" aria-label="关闭操作菜单" className="mobile-overlay-backdrop" onClick={onCancel} />
      <div ref={ref} role="dialog" aria-modal="true" tabIndex={-1} className="mobile-action-sheet" aria-label={title}>
        <div className="mobile-action-group glass">
          {title && <div className="mobile-action-title">{title}</div>}
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`mobile-action-option ${option.destructive ? "danger" : ""} ${option.checked ? "checked" : ""}`}
              onClick={() => onSelect(option.value)}
            >
              <span>{option.label}</span>
              {option.checked && <MobileIcon name="check" className="size-[18px]" />}
            </button>
          ))}
        </div>
        <button type="button" className="mobile-action-cancel glass" onClick={onCancel}>
          取消
        </button>
      </div>
    </div>,
    document.body,
  );
}

/** 玻璃确认框 —— 危险操作必须二次确认。 */
export function ConfirmDialog({
  open,
  message,
  confirmLabel = "删除",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useDialogLifecycle(open, onCancel, ref);
  if (!open) {
    return null;
  }
  return createPortal(
    <div className="mobile-overlay">
      <button type="button" aria-label="关闭确认框" className="mobile-overlay-backdrop" onClick={onCancel} />
      <div ref={ref} role="alertdialog" aria-modal="true" tabIndex={-1} className="mobile-confirm glass">
        <p>{message}</p>
        <div className="mobile-confirm-actions">
          <button type="button" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="danger" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
