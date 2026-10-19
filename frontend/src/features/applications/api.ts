import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type ApplicationSummary = components["schemas"]["ApplicationSummary"];
/** 列表响应整体 —— `total` 与 `has_next` 支撑真分页。 */
export type ApplicationList = components["schemas"]["ApplicationListResponse"];
/** 契约里的阶段枚举（后端 `ApplicationStage`）—— 现已进契约，不再是魔法字符串。 */
export type ApplicationStage = components["schemas"]["ApplicationStage"];

export const STAGE_LABELS: Record<ApplicationStage, string> = {
  applied: "已投递",
  written_test: "笔试",
  interview: "面试",
  offer: "Offer",
  rejected: "已拒",
  withdrawn: "已放弃",
};

export const STAGE_ORDER: ApplicationStage[] = [
  "applied",
  "written_test",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

/**
 * 备注的长度上限 —— 与后端 `applications/schemas.MAX_NOTE_CHARS` 一致。
 *
 * 契约里带了这个上限（`StageUpdateRequest.note.maxLength`），但生成的类型只有**类型**、
 * 取不到字面量，所以这里抄一份；测试拿契约快照逐条比对，抄错就红（与 ProfileEditor
 * 的四个上限同一手法）。
 */
export const MAX_NOTE_CHARS = 2000;

export const APPLICATIONS_PAGE_SIZE = 50;

type ApplicationCache = ApplicationList | InfiniteData<ApplicationList>;

/** 同一个列表既可能被桌面端按页缓存，也可能被手机端按无限页缓存 —— 两处都要收敛。 */
function updateApplicationCaches(
  queryClient: ReturnType<typeof useQueryClient>,
  applicationId: string,
  update: (row: ApplicationSummary) => ApplicationSummary,
) {
  queryClient.setQueriesData<ApplicationCache>({ queryKey: ["applications"] }, (previous) => {
    if (!previous) {
      return previous;
    }
    if ("pages" in previous) {
      return {
        ...previous,
        pages: previous.pages.map((page) => ({
          ...page,
          items: page.items.map((row) => (row.id === applicationId ? update(row) : row)),
        })),
      };
    }
    return {
      ...previous,
      items: previous.items.map((row) => (row.id === applicationId ? update(row) : row)),
    };
  });
}

export async function fetchApplications(page: number): Promise<ApplicationList> {
  const { data, error, response } = await api.GET("/api/applications", {
    params: { query: { page, page_size: APPLICATIONS_PAGE_SIZE } },
  });
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "加载投递记录失败", response));
  }
  // 整份响应（含 `total` / `page` / `has_next`）交出去 —— 只留 `items` 会让分页
  // 元信息在全链路丢失，页面只能拿 `items.length` 冒充「共 N 条」。
  return data;
}

/** 手机端滚动加载：投递记录通常不多，但分页边界不能靠「第一页恰好够用」。 */
export function useInfiniteApplications() {
  return useInfiniteQuery({
    queryKey: ["applications", "infinite"],
    queryFn: ({ pageParam }) => fetchApplications(pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_next ? last.page + 1 : undefined),
  });
}

export function useApplications(page = 1) {
  return useQuery({
    queryKey: ["applications", { page }],
    queryFn: () => fetchApplications(page),
    placeholderData: (previous) => previous,
  });
}

export function useChangeStage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { applicationId: string; stage: string }) => {
      const { data, error, response } = await api.POST(
        "/api/applications/{application_id}/stage",
        {
          params: { path: { application_id: input.applicationId } },
          // 只提交 `stage`，**刻意不带 `note`**：这个端点是整份状态的写面，带上 note
          // 就会拿查询缓存里的旧值覆盖用户在备注框里刚写的内容（同一行「先填备注、
          // 再改阶段」时必现 —— blur 先发一条备注请求，紧接着这里又把陈旧 note 发一遍）。
          // 省略 note 字段＝后端保留原备注（见 `service.change_stage` 的三种取值语义）。
          //
          // `<select>` 的值天生是 string，在**这里**收窄为契约枚举即可 ——
          // 调用方的类型不必被 DOM 的宽 string 污染。
          body: { stage: input.stage as ApplicationStage },
        },
      );
      if (error || !data) {
        throw new Error(apiErrorMessage(error, "改阶段失败", response));
      }
      return data;
    },
    onSuccess: (data, input) => {
      // 先把本轮结果**收敛进查询缓存**，再让组件撤掉乐观草稿 —— 组件的 per-call
      // onSuccess 在本钩子的 onSuccess **之后**运行（@tanstack/react-query v5 的
      // `Mutation.execute` 先 await `options.onSuccess`，再 dispatch 触发观察者回调）。
      //
      // 为什么必须收敛：只撤草稿不收敛的话，撤草稿那一刻 `select` 的取值会回落到
      // 查询数据里**旧**的 `stage`（invalidate 触发的 refetch 还是异步的）——
      // 下拉框先跳回旧值、再跳到新值，看起来像「系统把选择吞了又吐出来」。
      // 把权威值写进缓存后再撤草稿，两者落在同一批更新里，中间帧不存在。
      updateApplicationCaches(queryClient, input.applicationId, (row) => ({
        ...row,
        stage: data.stage,
      }));
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}

/**
 * 保存一条投递的备注。
 *
 * 端点与改阶段是同一个（`POST /applications/{id}/stage` 同时收 `stage` 与 `note`），
 * 但**单开一个 mutation**：与改阶段共用一个时，两种操作的失败会落到同一个 `isError`
 * 上 —— 用户改备注失败却看到「改阶段失败」，而先失败的那条的提示会被后一条顶掉。
 * 请求体带上**当前**阶段（乐观锁要求整份状态一起提交）。
 */
export function useSaveNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      applicationId: string;
      stage: string;
      note: string;
    }) => {
      const { data, error, response } = await api.POST(
        "/api/applications/{application_id}/stage",
        {
          params: { path: { application_id: input.applicationId } },
          // 备注**原样**提交：空串＝清掉（后端把它规范化为 NULL，界面回到空框）。
          // 这里**不能**把空串转成 `null` —— `null` 在后端是「保留原备注」的意思，
          // 那样「清空备注」会变成一次静默的空操作（框清空了、提交后旧备注又回来）。
          body: { stage: input.stage as ApplicationStage, note: input.note },
        },
      );
      if (error || !data) {
        throw new Error(apiErrorMessage(error, "保存备注失败", response));
      }
      return data;
    },
    onSuccess: (_data, input) => {
      // 先把本轮提交的 note 收敛进查询缓存，再让组件撤掉草稿（与 useChangeStage 同款，
      // 见那里的详细说明）。只 invalidate 不够：组件在 onSuccess 里立刻 `clearNoteDraft`，
      // 那一刻输入框 value 回落到缓存里的**旧** note（invalidate 触发的 refetch 还是
      // 异步的），表现为「保存成功后输入框弹回旧内容」—— 正是 useChangeStage 注释里
      // 描述并修掉的同类问题，此前备注路径漏了对应收敛。
      //
      // `StageUpdateResponse` 只有 id + stage（后端不回 note），所以这是基于**提交值**
      // 的乐观写入。它与后端语义一致：空白备注被 `service.change_stage` 规范化为 NULL，
      // 因此空串在这里也写 null（否则「清空备注」会先把框显示成空、再被 refetch 拉回旧值）。
      updateApplicationCaches(queryClient, input.applicationId, (row) => ({
        ...row,
        note: input.note || null,
      }));
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}

export function useDeleteApplication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (applicationId: string) => {
      const { error, response } = await api.DELETE(
        "/api/applications/{application_id}",
        {
          params: { path: { application_id: applicationId } },
        },
      );
      if (error) {
        throw new Error(apiErrorMessage(error, "删除失败", response));
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
}

/**
 * 记录一次投递。
 *
 * 归 applications feature 而不是 jobs：投递记录的本体在这里，岗位页只是**调用方**
 * （入口挂在 `POST /api/applications`，请求体带 job_id）。
 */
export function useApplyToJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { data, error, response } = await api.POST("/api/applications", {
        body: { job_id: jobId },
      });
      if (error || !data) {
        throw new Error(apiErrorMessage(error, "记录投递失败", response));
      }
      return data;
    },
    onSuccess: () => {
      // 新投递落在 applications；岗位页的投递按钮状态也要跟着刷新
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
