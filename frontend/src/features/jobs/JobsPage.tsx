import { useState } from "react";

import { ErrorNotice, PageHeader, Pager, SuccessNotice } from "../../shared/ui";
import { useScoreJob } from "../scoring/api";
import { useApplyToJob } from "../applications/api";
import { useJobs } from "./api";
import { JobsTable } from "./components/JobsTable";
import { SearchBar } from "./components/SearchBar";

export function JobsPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const jobs = useJobs(keyword, page);
  const apply = useApplyToJob();
  const score = useScoreJob();

  return (
    // 这里是**页面内容**，不是文档主地标：AppLayout 已经渲染了 <main>，
    // 再嵌一层就是两个 main 地标（无效嵌套，屏幕阅读器的跳转菜单里出现两个
    // 「主要内容」）。其余四个页面都是 <div>，只有这里曾写成 <main>。
    <div className="mx-auto max-w-5xl px-4 py-8">
      {/* 页头与提示都走 `shared/ui` 的原语 —— 原先这里是手写的 h1/span/提示条，
          等于把设计系统的排版与配色又抄了一遍：改一处视觉要记得改两处。 */}
      <PageHeader
        title="岗位库"
        actions={
          <span className="text-sm text-muted">
            {jobs.data
              ? `共 ${jobs.data.total} 条`
              : jobs.isError
                ? // 与主体一致：读取失败时标题栏不能说「加载中…」—— 它永远不会变，
                  // 会与下面的错误提示长期矛盾。
                  "读取失败"
                : "加载中…"}
          </span>
        }
      />

      <SearchBar
        initial={keyword}
        onSearch={(value) => {
          setKeyword(value);
          setPage(1);
        }}
      />

      {jobs.isError ? (
        <div className="mt-6">
          <ErrorNotice message={(jobs.error as Error).message} />
        </div>
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

      {/* 页码用**本地 state**，不用 `jobs.data.page`：placeholderData 保留上一页
          数据时服务端回的 page 仍是旧值，Pager 据此把「下一页」算成同一个目标页 ——
          连点第二下 setPage 值不变、静默无响应（要等首个请求回来才恢复）。 */}
      <Pager
        ariaLabel="岗位分页"
        page={page}
        hasNext={jobs.data?.has_next ?? false}
        busy={jobs.isPlaceholderData}
        onPageChange={setPage}
      />

      {apply.isError && (
        <div className="mt-2">
          <ErrorNotice message={(apply.error as Error).message} />
        </div>
      )}
      {apply.isSuccess && (
        <div className="mt-2">
          <SuccessNotice message="已记录投递。" />
        </div>
      )}

      {/* 评分失败原因原样透出：最常见的是「画像未配置」，后端已给可行动指引 */}
      {score.isError && (
        <div className="mt-2">
          <ErrorNotice message={(score.error as Error).message} />
        </div>
      )}
      {score.isSuccess && (
        <div className="mt-2">
          <SuccessNotice message="已评分并写回。" />
        </div>
      )}
    </div>
  );
}
