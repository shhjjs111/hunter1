import { useRef, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Tag,
  fieldClass,
} from "../../shared/ui";
import {
  MAX_NOTE_CHARS,
  STAGE_LABELS,
  STAGE_ORDER,
  useApplications,
  useChangeStage,
  useDeleteApplication,
  useSaveNote,
  type ApplicationSummary,
} from "./api";

/**
 * 把 ISO 时间戳按**本地时区**格式化为 YYYY-MM-DD。
 *
 * 不能用 `updated_at.slice(0, 10)`：那取的是 UTC 日期，UTC+8 的
 * 00:00–07:59 会显示成前一天。后端发的是带时区的 ISO 串，交给 Date 本地化即可。
 */
function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function ApplicationsPage() {
  const applications = useApplications();
  const changeStage = useChangeStage();
  const remove = useDeleteApplication();
  const saveNote = useSaveNote();

  // 列表有固定上限（后端 `LIST_LIMIT`）。用响应里的 `total` / `has_more` 显示真实
  // 条数并提示截断 —— 拿 `items.length` 冒充「共 N 条」会让用户以为投递只有 200 条。
  const items = applications.data?.items ?? [];
  const total = applications.data?.total ?? 0;
  const shown = items.length;
  const truncated = applications.data?.has_more ?? false;
  const subtitle = applications.data
    ? truncated
      ? `共 ${total} 条，只显示最近 ${shown} 条`
      : `共 ${total} 条`
    : applications.isError
      ? // 读取失败时不能说「加载中…」：那句会与下面的 ErrorNotice 矛盾，
        // 而且它会永久停在那里（失败后 isLoading 转 false，不会再变）。
        "读取失败"
      : "加载中…";

  // 阶段选择的**乐观值**：不设它的话，用户在下拉里选中的值在请求回来前会被
  // 服务端旧值覆盖（视觉回弹），失败时选择被静默撤销 —— 两种都像「点了没反应」。
  // 失败时清掉草稿，让选择回落到服务端真值，并保留上面的 ErrorNotice 提示。
  const [stageDraft, setStageDraft] = useState<Record<string, string>>({});
  // 每行「最近一次提交的阶段」。并发改同一行时由它裁定谁能撤销草稿：
  // 没有它的话，先失败的那次 onError 会删掉后一次刚写入的乐观值（下拉框回弹到
  // 旧值，而用户刚选的新值看起来被系统吞了）。
  const latestRequest = useRef<Record<string, string>>({});

  function clearDraft(applicationId: string) {
    delete latestRequest.current[applicationId];
    setStageDraft((prev) => {
      const next = { ...prev };
      delete next[applicationId];
      return next;
    });
  }

  // ---- 备注 ----
  //
  // 备注是一列**可编辑**的文本（此前只显示、没有任何填写入口：改阶段的请求把
  // `item.note`（恒为 null）原样回传，于是那一列永远是「—」）。草稿与提交规则：
  // 敲完回车或点到别处就保存；**没改过不发请求**；失败时**保留用户输入**。
  const [noteDraft, setNoteDraft] = useState<Record<string, string>>({});
  // 每行最近一次提交的备注：onSuccess 只有在草稿仍是这一次提交的值时才清掉它 ——
  // 否则会把用户随后敲的新内容一起抹掉（与阶段草稿的「最新意图」同一考量）。
  const latestNote = useRef<Record<string, string>>({});

  function clearNoteDraft(applicationId: string) {
    delete latestNote.current[applicationId];
    setNoteDraft((prev) => {
      const next = { ...prev };
      delete next[applicationId];
      return next;
    });
  }

  function commitNote(item: ApplicationSummary) {
    const draft = noteDraft[item.id];
    if (draft === undefined) {
      return; // 这一行没动过
    }
    const note = draft.trim();
    if (note === (item.note ?? "")) {
      clearNoteDraft(item.id); // 又改回原样 → 不发请求，草稿交还给服务端值
      return;
    }
    latestNote.current[item.id] = note;
    saveNote.mutate(
      { applicationId: item.id, stage: item.stage, note },
      {
        onSuccess: () => {
          if (latestNote.current[item.id] === note) {
            clearNoteDraft(item.id);
          }
        },
        // 失败：**留着草稿**。用户刚敲的字不能因为一次网络失败就消失（与「保存失败时
        // 密钥输入框不清空」同一个取舍）；库里那份没被改动，错误另有 ErrorNotice，
        // 用户再触发一次提交即可重试。
      },
    );
  }

  return (
    <>
      <PageHeader title="投递记录" subtitle={subtitle} />

      {/* 列表的三种状态**互斥**：读取失败时绝不能再渲染「还没有投递记录」空态 ——
          那会把「读失败」讲成「你没有数据」，用户于是不会去重试（JobsPage 早已是
          互斥分支，这里与之对齐）。阶段/删除的失败是**另一次操作**的错误，另列。 */}
      {applications.isError ? (
        <ErrorNotice message={(applications.error as Error).message} />
      ) : applications.isLoading ? (
        // 显式加载态：否则首帧会渲染一张空表，与「一条都没有」看起来一样
        <Card>
          <p className="px-4 py-6 text-sm text-muted">加载中…</p>
        </Card>
      ) : items.length === 0 ? (
        <EmptyState>还没有投递记录。去「岗位库」找岗位，点「记录投递」。</EmptyState>
      ) : (
        <Card>
          {/* 窄屏可横向滚动：与岗位表同理 —— 宁可横滑，也不把「2026-10-06」
              这类内容压成竖排。 */}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm whitespace-nowrap">
              <thead>
                <tr className="border-b border-line text-left text-muted">
                  <th className="px-4 py-2 font-medium">公司</th>
                  <th className="px-4 py-2 font-medium">岗位</th>
                  <th className="px-4 py-2 font-medium">阶段</th>
                  <th className="px-4 py-2 font-medium">更新</th>
                  <th className="px-4 py-2 font-medium">备注</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-line-soft last:border-0">
                    <td className="px-4 py-2">{item.company}</td>
                    <td className="px-4 py-2">{item.title}</td>
                    <td className="px-4 py-2">
                      <select
                        className="rounded border border-field bg-surface px-2 py-1"
                        aria-label={`「${item.title}」（${item.company}）的投递阶段`}
                        value={stageDraft[item.id] ?? item.stage}
                        onChange={(event) => {
                          const stage = event.target.value;
                          latestRequest.current[item.id] = stage;
                          setStageDraft((prev) => ({ ...prev, [item.id]: stage }));
                          changeStage.mutate(
                            {
                              applicationId: item.id,
                              stage,
                              note: item.note ?? undefined,
                            },
                            {
                              // 成功：这条草稿已经等于服务端真值 → 交还给查询数据。
                              // 不清理的话它会**永久**压住后续 refetch 的结果
                              // （包括在别处改出来的新阶段）。
                              onSuccess: () => {
                                if (latestRequest.current[item.id] === stage) {
                                  clearDraft(item.id);
                                }
                              },
                              // 失败：撤销乐观值，选择回到服务端真值（错误另有
                              // ErrorNotice）。只在这仍是**最新意图**时撤销 ——
                              // 否则会把用户后来那次的选择一起抹掉。
                              //
                              // 实测说明：@tanstack/react-query v5 的
                              // `MutationObserver.mutate()` 会在发起新调用前
                              // `removeObserver`，所以同一行连改两次时**前一次的回调
                              // 根本不会被调用**、这条守卫在今天不可达。留着它是因为
                              // 「最新意图」的判断本身是这段逻辑的正确性条件：
                              // 一旦换用 mutationCache / 换回 v4 语义，缺了它就真会
                              // 把用户后一次的选择抹掉。
                              onError: () => {
                                if (latestRequest.current[item.id] === stage) {
                                  clearDraft(item.id);
                                }
                              },
                            },
                          );
                        }}
                      >
                        {STAGE_ORDER.map((stage) => (
                          <option key={stage} value={stage}>
                            {STAGE_LABELS[stage]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-2 text-muted">{formatDate(item.updated_at)}</td>
                    <td className="px-4 py-2">
                      <input
                        className={`w-full min-w-40 ${fieldClass}`}
                        aria-label={`「${item.title}」（${item.company}）的备注`}
                        maxLength={MAX_NOTE_CHARS}
                        placeholder="加一条备注…"
                        value={noteDraft[item.id] ?? item.note ?? ""}
                        onChange={(event) =>
                          setNoteDraft((prev) => ({ ...prev, [item.id]: event.target.value }))
                        }
                        // 失焦即保存（回车也走这里：blur 只有一个提交入口，避免两处各写一遍）
                        onBlur={() => commitNote(item)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            event.currentTarget.blur();
                          }
                        }}
                      />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Button
                        onClick={() => {
                          // 删除不可撤销（后端无软删）—— 先确认，避免误点丢记录
                          if (
                            window.confirm(
                              `删除「${item.company}」的投递记录？此操作不可撤销。`,
                            )
                          ) {
                            remove.mutate(item.id);
                          }
                        }}
                      >
                        删除
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* 阶段变更 / 删除 / 备注保存的失败是**另一次操作**的错误，与列表状态无关，单独列在这里。
          三者的提示分开渲染：合成一条会让「改备注失败」看起来像「改阶段失败」。 */}
      {changeStage.isError && <ErrorNotice message={(changeStage.error as Error).message} />}
      {saveNote.isError && <ErrorNotice message={(saveNote.error as Error).message} />}
      {remove.isError && <ErrorNotice message={(remove.error as Error).message} />}

      <p className="mt-4 text-xs text-muted">
        投递记录里的公司名与岗位名是<b>下单时刻的快照</b> —— 岗位被重抓或改名时，
        这里仍保留当时的说法。
      </p>
      <Tag>只读提示：阶段可直接在下拉框里改；备注敲完回车（或点到别处）即保存；删除不可撤销。</Tag>
    </>
  );
}
