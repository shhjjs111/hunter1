import { Button } from "../../../shared/ui";

/**
 * 分页控件（纯展示）。首页且无下一页时不渲染。
 *
 * 按钮走 `shared/ui` 的 `Button`：这里原先手写了一份边框/悬停/禁用样式，
 * 与 `Button.default` 逐条重合（只是 py-1 vs py-1.5、opacity-40 vs 50）——
 * 同一语义两处定义，改视觉时容易只改一处。
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
    <nav aria-label="岗位分页" className="mt-4 flex items-center gap-3 text-sm">
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
