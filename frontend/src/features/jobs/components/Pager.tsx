/**
 * 分页控件（纯展示）。首页且无下一页时不渲染。
 */
export function Pager({
  page,
  hasNext,
  onPageChange,
}: {
  page: number;
  hasNext: boolean;
  onPageChange: (page: number) => void;
}) {
  if (page <= 1 && !hasNext) {
    return null;
  }
  return (
    <nav className="mt-4 flex items-center gap-3 text-sm">
      <button
        type="button"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-40"
      >
        ← 上一页
      </button>
      <span className="text-slate-500">第 {page} 页</span>
      <button
        type="button"
        disabled={!hasNext}
        onClick={() => onPageChange(page + 1)}
        className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-40"
      >
        下一页 →
      </button>
    </nav>
  );
}
