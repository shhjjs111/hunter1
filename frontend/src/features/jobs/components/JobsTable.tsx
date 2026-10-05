import type { JobSummary } from "../api";

/**
 * 岗位表（纯展示）—— 数据与回调全由 props 注入，测试它可以零 mock。
 */
export function JobsTable({
  jobs,
  loading = false,
  applyingId,
  onApply,
}: {
  jobs: JobSummary[];
  loading?: boolean;
  applyingId?: string;
  onApply: (jobId: string) => void;
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
    <table className="mt-6 w-full border-collapse text-sm">
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
            <td className="py-2 pr-4">
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
              <button
                type="button"
                className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
                disabled={applyingId === job.id}
                onClick={() => onApply(job.id)}
              >
                {applyingId === job.id ? "记录中…" : "记录投递"}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
