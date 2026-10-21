import { useEffect, useMemo, useRef, useState } from "react";

import { useMobileChrome } from "../../app/mobileChrome";
import { useRowTasks } from "../../shared/useRowTasks";
import { ActionSheet, ConfirmDialog } from "../../shared/ui/mobile";
import {
  STAGE_LABELS,
  STAGE_ORDER,
  useChangeStage,
  useDeleteApplication,
  useInfiniteApplications,
  useSaveNote,
  type ApplicationStage,
  type ApplicationSummary,
} from "./api";
import { ApplicationCard } from "./components/ApplicationCard";

type Filter = "all" | ApplicationStage;

export function MobileApplicationsPage() {
  const { setOverlayOpen, showToast } = useMobileChrome();
  const applications = useInfiniteApplications();
  const changeStage = useChangeStage();
  const remove = useDeleteApplication();
  const saveNote = useSaveNote();
  const stageTask = useRowTasks();
  const noteTask = useRowTasks();
  const removeTask = useRowTasks();
  const [filter, setFilter] = useState<Filter>("all");
  const [stageOverrides, setStageOverrides] = useState<Record<string, ApplicationStage>>({});
  const [stageTargetId, setStageTargetId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ApplicationSummary | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const items = useMemo(
    () => applications.data?.pages.flatMap((page) => page.items) ?? [],
    [applications.data],
  );
  const total = applications.data?.pages[0]?.total ?? 0;
  const stageOf = (application: ApplicationSummary) =>
    stageOverrides[application.id] ?? application.stage;
  const counts = useMemo(() => {
    const result = new Map<ApplicationStage, number>();
    for (const application of items) {
      const stage = stageOverrides[application.id] ?? application.stage;
      result.set(stage, (result.get(stage) ?? 0) + 1);
    }
    return result;
  }, [items, stageOverrides]);
  const visible = items.filter((application) => filter === "all" || stageOf(application) === filter);
  const interviews = counts.get("interview") ?? 0;
  const subtitle = applications.data
    ? `共 ${total} 条 · ${interviews} 场面试待跟进`
    : applications.isError
      ? "读取失败"
      : "加载中…";

  const overlayOpen = stageTargetId !== null || deleteTarget !== null;
  useEffect(() => {
    setOverlayOpen(overlayOpen);
    return () => setOverlayOpen(false);
  }, [overlayOpen, setOverlayOpen]);

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !applications.hasNextPage || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting) && !applications.isFetchingNextPage) {
          void applications.fetchNextPage();
        }
      },
      { rootMargin: "240px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [applications]);

  const stageTarget = items.find((application) => application.id === stageTargetId) ?? null;

  function selectStage(value: string) {
    const target = stageTarget;
    setStageTargetId(null);
    if (!target) {
      return;
    }
    if (value === "delete") {
      setDeleteTarget(target);
      return;
    }
    const stage = value as ApplicationStage;
    if (stage === target.stage || !stageTask.begin(target.id)) {
      return;
    }
    setStageOverrides((prev) => ({ ...prev, [target.id]: stage }));
    changeStage.mutate(
      { applicationId: target.id, stage },
      {
        onSuccess: () => {
          setStageOverrides((prev) => {
            const next = { ...prev };
            delete next[target.id];
            return next;
          });
          showToast("success", `已改为「${STAGE_LABELS[stage]}」`);
        },
        onError: (error) => {
          setStageOverrides((prev) => {
            const next = { ...prev };
            delete next[target.id];
            return next;
          });
          showToast("error", error.message);
        },
        onSettled: () => stageTask.settle(target.id),
      },
    );
  }

  function confirmDelete() {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (!target) {
      return;
    }
    if (!removeTask.begin(target.id)) {
      return;
    }
    setDeletingId(target.id);
    remove.mutate(target.id, {
      onSuccess: () => {
        showToast("success", "已删除");
      },
      onError: (error) => {
        setDeletingId(null);
        showToast("error", error.message);
      },
      onSettled: () => removeTask.settle(target.id),
    });
  }

  return (
    <div>
      <h1 className="mobile-title">投递记录</h1>
      <p className="mobile-subtitle num">{subtitle}</p>

      <div className="mobile-chips mobile-chips-bar">
        <button
          type="button"
          className={`mobile-chip ${filter === "all" ? "on" : ""}`}
          onClick={() => setFilter("all")}
        >
          全部<span className="count">{total}</span>
        </button>
        {STAGE_ORDER.map((stage) => (
          <button
            key={stage}
            type="button"
            className={`mobile-chip ${filter === stage ? "on" : ""}`}
            onClick={() => setFilter(stage)}
          >
            {STAGE_LABELS[stage]}
            <span className="count">{counts.get(stage) ?? 0}</span>
          </button>
        ))}
      </div>

      {applications.isError ? (
        <p className="mobile-error">{(applications.error as Error).message}</p>
      ) : (
        <div className="mobile-application-list">
          {applications.isLoading &&
            Array.from({ length: 2 }, (_, index) => (
              <div className="mobile-skeleton" key={index} aria-hidden="true" />
            ))}
          {visible.map((application) => (
            <ApplicationCard
              key={application.id}
              application={application}
              stage={stageOf(application)}
              deleting={deletingId === application.id}
              onOpenStage={() => setStageTargetId(application.id)}
              onSaveNote={(note) => {
                if (!noteTask.begin(application.id)) {
                  return;
                }
                saveNote.mutate(
                  { applicationId: application.id, stage: stageOf(application), note },
                  {
                    onSuccess: () => showToast("success", "备注已保存。"),
                    onError: (error) => {
                      noteTask.setError(application.id, error.message);
                      showToast("error", error.message);
                    },
                    onSettled: () => noteTask.settle(application.id),
                  },
                );
              }}
            />
          ))}
          {!applications.isLoading && visible.length === 0 && (
            <div className="mobile-empty">
              {items.length === 0
                ? "还没有投递记录。去「岗位库」找岗位，点「记录投递」。"
                : "这个阶段还没有投递。"}
            </div>
          )}
          <div ref={sentinelRef} className="mobile-sentinel" aria-hidden="true" />
          {applications.hasNextPage && (
            <button
              type="button"
              className="mobile-button block"
              disabled={applications.isFetchingNextPage}
              onClick={() => void applications.fetchNextPage()}
            >
              {applications.isFetchingNextPage ? "加载中…" : "加载更多"}
            </button>
          )}
        </div>
      )}

      <p className="mobile-foot">点阶段标签可改阶段或删除 · 公司名与岗位名是投递时刻的快照</p>

      <ActionSheet
        open={stageTarget !== null}
        title={stageTarget ? `${stageTarget.company} · ${stageTarget.title}\n修改投递阶段` : undefined}
        options={
          stageTarget
            ? [
                ...STAGE_ORDER.map((stage) => ({
                  label: STAGE_LABELS[stage],
                  value: stage,
                  checked: stageOf(stageTarget) === stage,
                })),
                { label: "删除这条记录", value: "delete", destructive: true },
              ]
            : []
        }
        onSelect={selectStage}
        onCancel={() => setStageTargetId(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        message={deleteTarget ? `删除「${deleteTarget.company}」的投递记录？此操作不可撤销。` : ""}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
