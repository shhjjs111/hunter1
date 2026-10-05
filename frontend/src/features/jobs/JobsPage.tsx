import { useState } from "react";

import { useApplyToJob, useJobs } from "./api";
import { JobsTable } from "./components/JobsTable";
import { Pager } from "./components/Pager";
import { SearchBar } from "./components/SearchBar";

export function JobsPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const jobs = useJobs(keyword, page);
  const apply = useApplyToJob();

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
          onApply={(jobId) => apply.mutate(jobId)}
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
    </main>
  );
}
