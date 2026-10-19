import type { KeyboardEvent, MouseEvent } from "react";

import type { JobSummary } from "../api";

function scoreClass(score: number | null | undefined): string {
  if (score == null) {
    return "none";
  }
  return score >= 80 ? "good" : "mid";
}

/** 手机端岗位卡：点正文看详情，底部按钮独立处理评分/投递。 */
export function JobCard({
  job,
  applied,
  scoring,
  applying,
  onOpen,
  onScore,
  onApply,
}: {
  job: JobSummary;
  applied: boolean;
  scoring: boolean;
  applying: boolean;
  onOpen: () => void;
  onScore: () => void;
  onApply: () => void;
}) {
  const stop = (event: MouseEvent<HTMLButtonElement>) => event.stopPropagation();
  const keyOpen = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen();
    }
  };
  return (
    <article
      className="mobile-card mobile-job-card"
      role="button"
      tabIndex={0}
      aria-label={`${job.title}，${job.company}，查看详情`}
      onClick={onOpen}
      onKeyDown={keyOpen}
    >
      <div className="mobile-job-head">
        <div className="min-w-0 flex-1">
          <div className="mobile-job-title">{job.title}</div>
          <div className="mobile-job-company">{job.company}</div>
        </div>
        <div className={`mobile-score ${scoreClass(job.match_score)}`}>
          {job.match_score ?? "未评分"}
        </div>
      </div>
      <div className="mobile-meta">
        {job.city && <span>{job.city}</span>}
        <span>{job.source}</span>
        {job.capture_status === "failed" && <span>抓取失败</span>}
      </div>
      <div className="mobile-row2">
        <button
          type="button"
          className="mobile-button"
          disabled={scoring}
          aria-label={`${job.match_score == null ? "评分" : "重新评分"}：${job.title}`}
          onClick={(event) => {
            stop(event);
            onScore();
          }}
        >
          {scoring ? (
            <>
              <span className="mobile-spinner" aria-hidden="true" />评分中…
            </>
          ) : job.match_score == null ? (
            "评分"
          ) : (
            "重新评分"
          )}
        </button>
        <button
          type="button"
          className="mobile-button"
          disabled={applied || applying}
          aria-label={`${applied ? "已投递" : "记录投递"}：${job.title}`}
          onClick={(event) => {
            stop(event);
            onApply();
          }}
        >
          {applied ? "已投递 ✓" : applying ? "记录中…" : "记录投递"}
        </button>
      </div>
    </article>
  );
}
