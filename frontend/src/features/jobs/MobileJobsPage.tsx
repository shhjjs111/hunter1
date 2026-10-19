import { useEffect, useMemo, useRef, useState } from "react";

import { useMobileChrome } from "../../app/mobileChrome";
import { MobileIcon, Sheet } from "../../shared/ui/mobile";
import { useApplyToJob, useApplications } from "../applications/api";
import { useScoreJob, useJobScore, type ScoreView } from "../scoring/api";
import { ScoreRing } from "../scoring/components/ScoreRing";
import { useCrawlStatus } from "../crawl/api";
import { useInfiniteJobs, useJobDetail, type JobSummary } from "./api";
import { JobCard } from "./components/JobCard";
import { safeHref } from "./url";

type Filter = "all" | "unscored" | "high";

export function MobileJobsPage() {
  const { animateTo, setOverlayOpen, showToast } = useMobileChrome();
  const [keyword, setKeyword] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const jobs = useInfiniteJobs(debounced);
  const applications = useApplications(1);
  const apply = useApplyToJob();
  const score = useScoreJob();
  const crawl = useCrawlStatus();
  const [appliedLocal, setAppliedLocal] = useState<Set<string>>(() => new Set());
  const [detailJob, setDetailJob] = useState<JobSummary | null>(null);
  const [scorePreview, setScorePreview] = useState<{ jobId: string; data: ScoreView } | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    setOverlayOpen(detailJob !== null);
    return () => setOverlayOpen(false);
  }, [detailJob, setOverlayOpen]);

  const items = useMemo(
    () => jobs.data?.pages.flatMap((page) => page.items) ?? [],
    [jobs.data],
  );
  const appliedIds = useMemo(() => {
    const ids = new Set(appliedLocal);
    for (const application of applications.data?.items ?? []) {
      ids.add(application.job_id);
    }
    return ids;
  }, [applications.data, appliedLocal]);
  const filtered = items.filter((job) => {
    if (filter === "unscored") {
      return job.match_score == null;
    }
    if (filter === "high") {
      return job.match_score != null && job.match_score >= 80;
    }
    return true;
  });

  const total = jobs.data?.pages[0]?.total ?? 0;
  const hasNext = jobs.hasNextPage;
  const unscored = items.filter((job) => job.match_score == null).length;
  const subtitle = jobs.data
    ? hasNext
      ? `共 ${total} 条 · 已加载 ${items.length} 条`
      : `共 ${total} 条 · ${unscored} 条未评分`
    : jobs.isError
      ? "读取失败"
      : "加载中…";

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasNext || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting) && !jobs.isFetchingNextPage) {
          void jobs.fetchNextPage();
        }
      },
      { rootMargin: "240px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNext, jobs]);

  const running = crawl.data?.running ?? false;
  const sites = crawl.data?.sites ?? [];
  const done = sites.filter((site) => site.status === "ok" || site.status === "failed").length;
  const progress = sites.length > 0 ? (done / sites.length) * 100 : 0;

  const jobDetail = useJobDetail(detailJob?.id ?? null);
  const storedScore = useJobScore(detailJob?.id ?? null);
  const detailScore =
    (scorePreview && scorePreview.jobId === detailJob?.id ? scorePreview.data : null) ?? storedScore.data ?? null;
  const matched = detailJob?.match_score ?? detailScore?.score ?? null;
  const href = detailJob ? safeHref(detailJob.detail_url) : null;

  function runScore(job: JobSummary) {
    score.mutate(job.id, {
      onSuccess: (data) => {
        setScorePreview({ jobId: job.id, data });
        showToast("success", `已评分：${data.score} 分`);
      },
      onError: (error) => showToast("error", error.message),
    });
  }

  function runApply(job: JobSummary) {
    apply.mutate(job.id, {
      onSuccess: () => {
        setAppliedLocal((prev) => new Set(prev).add(job.id));
        showToast("success", "已记录投递。");
      },
      onError: (error) => showToast("error", error.message),
    });
  }

  const scoringId = score.isPending ? score.variables : undefined;
  const applyingId = apply.isPending ? apply.variables : undefined;

  return (
    <div>
      <h1 className="mobile-title">岗位库</h1>
      <p className="mobile-subtitle num">{subtitle}</p>

      <label className="mobile-search mobile-no-swipe">
        <MobileIcon name="search" />
        <input
          type="search"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索岗位、公司"
          aria-label="搜索岗位"
        />
      </label>

      <button
        type="button"
        className={`mobile-live glass ${running ? "show" : ""}`}
        onClick={() => animateTo(1)}
        aria-hidden={!running}
        tabIndex={running ? 0 : -1}
      >
        <span className="mobile-live-dot" aria-hidden="true" />
        <span className="num">
          抓取中 {Math.min(sites.length || 1, done + 1)}/{sites.length || "?"} 站
        </span>
        <span className="mobile-live-bar" aria-hidden="true">
          <i style={{ width: `${progress}%` }} />
        </span>
        <span className="go">查看</span>
      </button>

      <div className="mobile-chips mobile-chips-bar">
        {(
          [
            ["all", "全部"],
            ["unscored", "未评分"],
            ["high", "80 分以上"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`mobile-chip ${filter === value ? "on" : ""}`}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {jobs.isError ? (
        <p className="mobile-error">{(jobs.error as Error).message}</p>
      ) : (
        <div className="mobile-job-list">
          {jobs.isLoading &&
            Array.from({ length: 3 }, (_, index) => (
              <div className="mobile-skeleton" key={index} aria-hidden="true" />
            ))}
          {!jobs.isLoading &&
            filtered.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                applied={appliedIds.has(job.id)}
                scoring={scoringId === job.id}
                applying={applyingId === job.id}
                onOpen={() => setDetailJob(job)}
                onScore={() => runScore(job)}
                onApply={() => runApply(job)}
              />
            ))}
          {!jobs.isLoading && filtered.length === 0 && (
            <div className="mobile-empty">
              {items.length === 0
                ? "没有找到岗位。换个关键词，或先去「抓取」跑一轮。"
                : "这个筛选下没有岗位。换一个筛选条件试试。"}
              {items.length === 0 && (
                <div className="mt-3">
                  <button type="button" className="mobile-button" onClick={() => animateTo(1)}>
                    去抓取
                  </button>
                </div>
              )}
            </div>
          )}
          <div ref={sentinelRef} className="mobile-sentinel" aria-hidden="true" />
          {hasNext && (
            <button
              type="button"
              className="mobile-button block"
              disabled={jobs.isFetchingNextPage}
              onClick={() => void jobs.fetchNextPage()}
            >
              {jobs.isFetchingNextPage ? "加载中…" : "加载更多"}
            </button>
          )}
        </div>
      )}

      <p className="mobile-foot">
        评分会消耗模型额度；结论会连同分数一起写回岗位。
      </p>

      <Sheet
        open={detailJob !== null}
        onClose={() => setDetailJob(null)}
        label={detailJob ? `${detailJob.title} 详情` : "岗位详情"}
        footer={
          detailJob && (
            <>
              {href ? (
                <a className="mobile-button" href={href} target="_blank" rel="noreferrer">
                  打开原网页
                  <MobileIcon name="external" className="size-4" />
                </a>
              ) : (
                <span className="mobile-button" aria-disabled="true">
                  无可用链接
                </span>
              )}
              {appliedIds.has(detailJob.id) ? (
                <button type="button" className="mobile-button primary" disabled>
                  已投递 ✓
                </button>
              ) : matched == null ? (
                <button
                  type="button"
                  className="mobile-button primary"
                  disabled={scoringId === detailJob.id}
                  onClick={() => runScore(detailJob)}
                >
                  {scoringId === detailJob.id ? "评分中…" : "评分"}
                </button>
              ) : (
                <button
                  type="button"
                  className="mobile-button primary"
                  disabled={applyingId === detailJob.id}
                  onClick={() => runApply(detailJob)}
                >
                  {applyingId === detailJob.id ? "记录中…" : "记录投递"}
                </button>
              )}
            </>
          )
        }
      >
        {detailJob && (
          <>
            <h2>{detailJob.title}</h2>
            <div className="mobile-sheet-company">
              {detailJob.company}
              {detailJob.city ? ` · ${detailJob.city}` : ""} · {detailJob.source}
            </div>
            <div className="mobile-card mobile-ring-box">
              <ScoreRing score={matched} />
              <div className="mobile-ring-copy">
                <strong>{matched == null ? "还没有评分" : `匹配分 ${matched}`}</strong>
                {matched == null
                  ? "评分后会写出摘要、优势和差距，告诉你这个分是怎么来的。"
                  : detailScore?.model
                    ? `模型：${detailScore.model}`
                    : "结论已写回岗位。"}
              </div>
            </div>
            {detailScore?.summary && (
              <>
                <div className="mobile-section-label">摘要</div>
                <p className="mobile-prose">{detailScore.summary}</p>
              </>
            )}
            {detailScore?.advantages && (
              <>
                <div className="mobile-section-label">优势</div>
                <p className="mobile-prose">{detailScore.advantages}</p>
              </>
            )}
            {detailScore?.gaps && (
              <>
                <div className="mobile-section-label">差距</div>
                <p className="mobile-prose">{detailScore.gaps}</p>
              </>
            )}
            {!detailScore?.summary && !detailScore?.advantages && !detailScore?.gaps && matched != null && (
              <p className="mobile-prose mt-3">模型这次只给了分数，没有写结论。</p>
            )}
            <div className="mobile-section-label">岗位描述</div>
            {jobDetail.isLoading ? (
              <p className="mobile-prose">正在加载 JD…</p>
            ) : jobDetail.isError ? (
              <p className="mobile-error">{(jobDetail.error as Error).message}</p>
            ) : (
              <p className="mobile-prose">
                {jobDetail.data?.jd_raw?.trim() || "这个岗位没有保存 JD 正文，可以打开原网页查看。"}
              </p>
            )}
          </>
        )}
      </Sheet>
    </div>
  );
}
