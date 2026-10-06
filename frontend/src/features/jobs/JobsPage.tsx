import { useState } from "react";

import { useScoreJob } from "../scoring/api";
import { useApplyToJob, useJobs } from "./api";
import { JobsTable } from "./components/JobsTable";
import { Pager } from "./components/Pager";
import { SearchBar } from "./components/SearchBar";

export function JobsPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const jobs = useJobs(keyword, page);
  const apply = useApplyToJob();
  const score = useScoreJob();

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6 flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold">岗位库</h1>
        <span className="text-sm text-slate-500">
          {jobs.data ? `共 ${jobs.data.total} 条` : "加载中…"}
        </span>
      </header>

      <SearchBar
        initial={keyword}
        onSearch={(value) => {
          setKeyword(value);
          setPage(1);
        }}
      />

      {jobs.isError ? (
        <p className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-red-700">
          {(jobs.error as Error).message}
        </p>
      ) : (
        <JobsTable
          jobs={jobs.data?.items ?? []}
          loading={jobs.isLoading}
          applyingId={apply.isPending ? apply.variables : undefined}
          scoringId={score.isPending ? score.variables : undefined}
          onApply={(jobId) => apply.mutate(jobId)}
          onScore={(jobId) => score.mutate(jobId)}
        />
      )}

      <Pager
        page={jobs.data?.page ?? 1}
        hasNext={jobs.data?.has_next ?? false}
        onPageChange={setPage}
      />

      {apply.isError && (
        <p className="mt-2 text-sm text-red-600">{(apply.error as Error).message}</p>
      )}
      {apply.isSuccess && <p className="mt-2 text-sm text-emerald-600">已记录投递。</p>}

      {/* 评分失败原因原样透出：最常见的是「画像未配置」，后端已给可行动指引 */}
      {score.isError && <p className="mt-2 text-sm text-red-600">{(score.error as Error).message}</p>}
      {score.isSuccess && <p className="mt-2 text-sm text-emerald-600">已评分并写回。</p>}
    </main>
  );
}
