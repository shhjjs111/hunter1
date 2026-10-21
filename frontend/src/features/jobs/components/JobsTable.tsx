import { Button, EmptyState } from "../../../shared/ui";
import type { JobSummary } from "../api";
import { safeHref } from "../url";

/**
 * 岗位表（纯展示）—— 数据与回调全由 props 注入，测试它可以零 mock。
 *
 * 空态与行内按钮都走 `shared/ui` 的原语：这两个位置原先各手写了一份边框与
 * 悬停样式，改视觉时得记得两处（而且容易只改一处）。
 */
/** 岗位名 + 详情链接：协议不是 http(s) 时退化成纯文本（见 safeHref）。 */
function JobTitleLink({ title, url }: { title: string; url: string }) {
  const href = safeHref(url);
  if (href === null) {
    return <span>{title}</span>;
  }
  return (
    <a className="font-medium text-ink decoration-current/40 underline-offset-4 hover:underline" href={href} target="_blank" rel="noreferrer">
      {title}
    </a>
  );
}

export function JobsTable({
  jobs,
  loading = false,
  applyingIds,
  scoringIds,
  scoreErrors = {},
  applyErrors = {},
  onApply,
  onScore,
}: {
  jobs: JobSummary[];
  loading?: boolean;
  applyingIds: ReadonlySet<string>;
  scoringIds: ReadonlySet<string>;
  scoreErrors?: Record<string, string>;
  applyErrors?: Record<string, string>;
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
    <div className="glass-thin mt-6 rounded-3xl p-2">
      <div className="scroll-fade overflow-x-auto">
        <table className="w-full border-separate border-spacing-0 text-sm whitespace-nowrap">
          <thead>
            <tr className="text-left text-xs tracking-wide text-muted">
              <th className="px-4 py-3 font-medium">岗位</th>
              <th className="px-4 py-3 font-medium">公司</th>
              <th className="px-4 py-3 font-medium">城市</th>
              <th className="px-4 py-3 font-medium">匹配分</th>
              <th className="px-4 py-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} className="group transition-colors hover:bg-fill-subtle [&>td]:border-t [&>td]:border-line-soft [&>td:first-child]:rounded-l-2xl [&>td:last-child]:rounded-r-2xl hover:[&>td]:border-transparent">
                {/* 行内按钮的**可访问名**保持「评分 / 记录投递」（不覆盖可见文案：
                    WCAG 2.5.3 要求可访问名包含可见标签），岗位信息经
                    `aria-describedby` 补上（支持空格分隔多个 id）—— 屏幕阅读器会念
                    「评分，AI产品经理 字节跳动」，视觉用户靠行位置区分，两边都不吃亏。 */}
                <td className="px-4 py-3.5" id={`job-${job.id}-label`}>
                  <JobTitleLink title={job.title} url={job.detail_url} />
                </td>
                <td className="px-4 py-3.5 text-ink-soft" id={`job-${job.id}-company`}>
                  {job.company}
                </td>
                <td className="px-4 py-3.5 text-muted-strong">{job.city ?? "—"}</td>
                <td className="px-4 py-3.5">
                  {job.match_score != null ? (
                    <span className="inline-flex min-w-10 justify-center rounded-full bg-success-soft px-2.5 py-0.5 font-semibold text-success tabular-nums">
                      {job.match_score}
                    </span>
                  ) : (
                    <span className="text-subtle">未评分</span>
                  )}
                </td>
                <td className="px-4 py-3.5 text-right">
                  <div className="flex flex-col items-end gap-1">
                    <div className="flex justify-end gap-2">
                      <Button
                        disabled={scoringIds.has(job.id)}
                        onClick={() => onScore(job.id)}
                        describedBy={`job-${job.id}-label job-${job.id}-company`}
                      >
                        {scoringIds.has(job.id) ? "评分中…" : "评分"}
                      </Button>
                      <Button
                        disabled={applyingIds.has(job.id)}
                        onClick={() => onApply(job.id)}
                        describedBy={`job-${job.id}-label job-${job.id}-company`}
                      >
                        {applyingIds.has(job.id) ? "记录中…" : "记录投递"}
                      </Button>
                    </div>
                    {scoreErrors[job.id] && (
                      <span role="alert" className="text-xs text-danger">
                        {scoreErrors[job.id]}
                      </span>
                    )}
                    {applyErrors[job.id] && (
                      <span role="alert" className="text-xs text-danger">
                        {applyErrors[job.id]}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
