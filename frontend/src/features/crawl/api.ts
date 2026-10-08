import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type CrawlStatus = components["schemas"]["CrawlStatusResponse"];

const POLL_MS = 1000;

/**
 * 轮询间隔的**策略**（纯函数，单独可测）。
 *
 * 收敛条件挂在「拿到明确结论」上，不挂在「成功数据」上：
 *
 * - `running: true` → 继续轮询；
 * - `running: false` → 停（后端明确说空闲了）；
 * - **快照缺失**（首次请求还没回来，或上一次请求失败）→ 继续轮询。
 *
 * 第三档是修出来的。原先写成 `query.state.data?.running ? POLL_MS : false`：
 * 一次瞬时失败（后端重启、代理闪断）拿不到快照，于是返回 `false`、**轮询永久停止**
 * —— 而后端 runner 还在跑（它不依赖这个页面）。界面的后果是：停在旧快照、按钮被
 * `running` 永久禁用、页面没有任何刷新入口，用户只能重启进程。这正是
 * `slices/crawl/runner.py` 立意要消灭的那种「静默失败」。
 */
export function crawlPollInterval(snapshot: { running: boolean } | undefined): number | false {
  if (snapshot === undefined) {
    return POLL_MS;
  }
  return snapshot.running ? POLL_MS : false;
}

async function fetchStatus(): Promise<CrawlStatus> {
  const { data, error, response } = await api.GET("/api/crawl/status");
  if (error || !data) {
    throw new Error(apiErrorMessage(error, "读取抓取进度失败", response));
  }
  return data;
}

/**
 * 抓取进度。**只在抓取进行中轮询** —— 空闲时每分钟一次就够，没必要每秒打后端。
 * （旧界面对快照的用法一致：开始抓取后才密集拉。）
 *
 * 「进行中」的判据见 `crawlPollInterval`：没有明确结论就继续问，而不是一击不中
 * 就永久停摆。代价是后端暂时不可达时会以 1s 的间隔重试 —— 换来的是「后端回来
 * 后界面自己恢复」，比「永远停在旧快照」划算。
 */
export function useCrawlStatus() {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);
  const query = useQuery({
    queryKey: ["crawl", "status"],
    queryFn: fetchStatus,
    refetchInterval: (query) => crawlPollInterval(query.state.data),
  });

  // 一轮抓取从「进行中」变成「结束」时失效岗位库。
  //
  // 抓完一轮岗位库的内容已经变了，而 `["jobs"]` 的 staleTime 是 30 秒：不失效的话，
  // 用户抓完立刻回岗位库看到的还是本轮之前的列表 —— 看起来像「抓了但没进来」，
  // 于是再抓一轮，白给站点添一次压力。
  const running = query.data?.running ?? false;
  useEffect(() => {
    if (wasRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    }
    wasRunning.current = running;
  }, [running, queryClient]);

  return query;
}

export function useStartCrawl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/crawl");
      if (error || !data) {
        throw new Error("启动抓取失败");
      }
      return data;
    },
    onSuccess: () => {
      // 必须重取状态：缓存里还是 `running: false`，而上面 useCrawlStatus 的
      // refetchInterval 只在 running 时轮询 —— 不重取就永远转不起来，
      // 界面最长 staleTime（30 秒）内毫无反应，按钮还恢复可点，
      // 用户再点只会看到「上一轮还在跑」。
      //
      // 立刻重取就能拿到 `running: true`：后端 `CrawlRunner.start()` 是**同步**
      // 置位后再返回的（见 slices/crawl/runner.py），所以这里没有竞态窗口。
      void queryClient.invalidateQueries({ queryKey: ["crawl", "status"] });
    },
  });
}
