import type { JobSummary } from "../api";

/**
 * 岗位表（纯展示）—— 数据与回调全由 props 注入，测试它可以零 mock。
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
    return <p className="mt-6 text-slate-500">正在加载岗位…</p>;
  }
  if (jobs.length === 0) {
    return (
      <div className="mt-6 rounded-lg border border-dashed border-slate-300 p-8 text-center text-slate-500">
        没有找到岗位。换个关键词，或先去「抓取」跑一轮。
      </div>
    );
  }
  return (
    // 窄屏可横向滚动：表格列有最小可读宽度，硬挤会把「北京」拆成「北 京」、
    // 「记录投递」折成两行。宁可横滑，也不压成竖排文字。
    <div className="mt-6 overflow-x-auto">
      <table className="w-full border-collapse text-sm whitespace-nowrap">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-500">
            <th className="py-2 pr-4 font-medium">岗位</th>
            <th className="py-2 pr-4 font-medium">公司</th>
            <th className="py-2 pr-4 font-medium">城市</th>
            <th className="py-2 pr-4 font-medium">匹配分</th>
            <th className="py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="border-b border-slate-100">
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
                  <span className="rounded bg-emerald-50 px-2 py-0.5 text-emerald-700">
                    {job.match_score}
                  </span>
                ) : (
                  <span className="text-slate-400">未评分</span>
                )}
              </td>
              <td className="py-2 text-right">
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
                    disabled={scoringId === job.id}
                    onClick={() => onScore(job.id)}
                    aria-describedby={`job-${job.id}-label`}
                  >
                    {scoringId === job.id ? "评分中…" : "评分"}
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
                    disabled={applyingId === job.id}
                    onClick={() => onApply(job.id)}
                    aria-describedby={`job-${job.id}-label`}
                  >
                    {applyingId === job.id ? "记录中…" : "记录投递"}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
