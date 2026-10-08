import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * 错误边界 —— 单个 feature 崩了不该把整个界面打成白屏。
 *
 * 本地工具没有远程日志：出错时把消息**显示在页面上**，用户能直接复制给
 * 维护者，而不是打开 devtools 才有线索。
 */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("界面渲染失败：", error, info.componentStack);
  }

  render() {
    if (this.state.error === null) {
      return this.props.children;
    }
    return (
      <div className="rounded-lg border border-danger-line bg-danger-soft p-6">
        <h2 className="mb-2 font-semibold text-danger-strong">这个页面出错了</h2>
        <pre className="overflow-auto text-xs text-danger">{this.state.error.message}</pre>
        <button
          type="button"
          className="mt-4 rounded border border-danger-field bg-surface px-3 py-1.5 text-sm"
          onClick={() => this.setState({ error: null })}
        >
          重试
        </button>
      </div>
    );
  }
}
