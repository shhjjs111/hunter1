import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorNotice, PageHeader, Pager, Toast } from "../../shared/ui";
import { useScoreJob } from "../scoring/api";
import { ScoreCard } from "../scoring/components/ScoreCard";
import { useApplyToJob } from "../applications/api";
import { useJobs } from "./api";
import { JobsTable } from "./components/JobsTable";
import { SearchBar } from "./components/SearchBar";

export function JobsPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState<{
    tone: "success" | "error";
    message: string;
  } | null>(null);
  const scoreCardRef = useRef<HTMLDivElement>(null);
  const jobs = useJobs(keyword, page);
  const apply = useApplyToJob();
  const score = useScoreJob();
  const dismissToast = useCallback(() => setToast(null), []);

  useEffect(() => {
    if (!score.isSuccess || score.data == null) {
      return;
    }
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const card = scoreCardRef.current;
    if (card !== null && typeof card.scrollIntoView === "function") {
      card.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "nearest",
      });
    }
  }, [score.data, score.isSuccess]);

  return (
    // 这里是**页面内容**，不是文档主地标：AppLayout 已经渲染了 <main>，
    // 再嵌一层就是两个 main 地标（无效嵌套，屏幕阅读器的跳转菜单里出现两个
    // 「主要内容」）。其余四个页面都是 <div>，只有这里曾写成 <main>。
    <div>
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
          // 换关键词＝换了一批岗位：上一次的投递/评分结果属于**旧上下文**。
          // 评分卡不显示岗位名（见 ScoreCard），留在屏上会与当前列表错配 ——
          // 用户无从判断那个分数属于谁。与 SettingsPage 对 probe/save 的处理同一取舍。
          apply.reset();
          score.reset();
          setToast(null);
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
          onApply={(jobId) =>
            apply.mutate(jobId, {
              onSuccess: () => setToast({ tone: "success", message: "已记录投递。" }),
              onError: (error) => setToast({ tone: "error", message: error.message }),
            })
          }
          onScore={(jobId) =>
            score.mutate(jobId, {
              onSuccess: (data) =>
                setToast({ tone: "success", message: `已评分：${data.score} 分` }),
              onError: (error) => setToast({ tone: "error", message: error.message }),
            })
          }
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
        onPageChange={(next) => {
          // 翻页同上：清掉属于上一页的投递/评分结果，不与新一页错配
          setPage(next);
          apply.reset();
          score.reset();
          setToast(null);
        }}
      />

      {score.isSuccess && score.data != null && (
        <div className="mt-2" ref={scoreCardRef}>
          {/* 不再是「已评分并写回。」一句话：后端已经把模型的结论（摘要 / 优势 /
              差距）随响应返回，这里把它摆出来 —— 用户才知道这个分是怎么来的。 */}
          <ScoreCard card={score.data} />
        </div>
      )}

      {toast !== null && (
        <Toast tone={toast.tone} message={toast.message} onDismiss={dismissToast} />
      )}
    </div>
  );
}
