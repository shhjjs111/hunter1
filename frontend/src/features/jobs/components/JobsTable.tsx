import { Button, EmptyState } from "../../../shared/ui";
import type { JobSummary } from "../api";

/**
 * 岗位表（纯展示）—— 数据与回调全由 props 注入，测试它可以零 mock。
 *
 * 空态与行内按钮都走 `shared/ui` 的原语：这两个位置原先各手写了一份边框与
 * 悬停样式，改视觉时得记得两处（而且容易只改一处）。
 */
export function JobsTable({
  jobs,
  loading = false,
  applyingId,
  scoringId,
  onApply,
  onScore,
}: {
  jobs: JobSummary[];
  loading?: boolean;
  applyingId?: string;
  scoringId?: string;
  onApply: (jobId: string) => void;
  onScore: (jobId: string) => void;
}) {
  if (loading) {
    return <p className="mt-6 text-muted">正在加载岗位…</p>;
  }
  if (jobs.length === 0) {
    return (
      <div className="mt-6">
        <EmptyState>没有找到岗位。换个关键词，或先去「抓取」跑一轮。</EmptyState>
      </div>
    );
  }
  return (
    // 窄屏可横向滚动：表格列有最小可读宽度，硬挤会把「北京」拆成「北 京」、
    // 「记录投递」折成两行。宁可横滑，也不压成竖排文字。
    <div className="mt-6 overflow-x-auto">
      <table className="w-full border-collapse text-sm whitespace-nowrap">
        <thead>
          <tr className="border-b border-line text-left text-muted">
            <th className="py-2 pr-4 font-medium">岗位</th>
            <th className="py-2 pr-4 font-medium">公司</th>
            <th className="py-2 pr-4 font-medium">城市</th>
            <th className="py-2 pr-4 font-medium">匹配分</th>
            <th className="py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="border-b border-line-soft">
              {/* 行内按钮的**可访问名**保持「评分 / 记录投递」（不覆盖可见文案：
                  WCAG 2.5.3 要求可访问名包含可见标签），岗位信息经
                  `aria-describedby` 补上 —— 屏幕阅读器会念「评分，AI产品经理 字节跳动」，
                  视觉用户靠行位置区分，两边都不吃亏。 */}
              <td className="py-2 pr-4" id={`job-${job.id}-label`}>
                <a
                  className="text-blue-700 hover:underline"
                  href={job.detail_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {job.title}
                </a>
              </td>
              <td className="py-2 pr-4">{job.company}</td>
              <td className="py-2 pr-4">{job.city ?? "—"}</td>
              <td className="py-2 pr-4">
                {job.match_score != null ? (
                  <span className="rounded bg-success-soft px-2 py-0.5 text-success">
                    {job.match_score}
                  </span>
                ) : (
                  <span className="text-subtle">未评分</span>
                )}
              </td>
              <td className="py-2 text-right">
                <div className="flex justify-end gap-2">
                  <Button
                    disabled={scoringId === job.id}
                    onClick={() => onScore(job.id)}
                    describedBy={`job-${job.id}-label`}
                    className="border border-field"
                  >
                    {scoringId === job.id ? "评分中…" : "评分"}
                  </Button>
                  <Button
                    disabled={applyingId === job.id}
                    onClick={() => onApply(job.id)}
                    describedBy={`job-${job.id}-label`}
                    className="border border-field"
                  >
                    {applyingId === job.id ? "记录中…" : "记录投递"}
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
