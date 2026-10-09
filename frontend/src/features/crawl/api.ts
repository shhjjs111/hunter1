import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { apiErrorMessage } from "../../shared/api/errors";
import type { components } from "../../shared/api/schema";

export type CrawlStatus = components["schemas"]["CrawlStatusResponse"];

const POLL_MS = 1000;
/** 轮询间隔上限（后端不可达时指数退避的封顶值）。 */
export const CRAWL_POLL_MAX_MS = 30_000;

/**
 * 轮询间隔的**策略**（纯函数，单独可测）。
 *
 * 收敛条件挂在「拿到明确结论」上，不挂在「成功数据」上：
 *
 * - `running: true` → 继续轮询；
 * - `running: false` **且没有未消化的失败** → 停（后端明确说空闲了）；
 * - 其余（快照缺失 / 这个快照之后还有失败）→ 继续轮询。
 *
 * 第三档是修出来的。原先写成 `query.state.data?.running ? POLL_MS : false`：
 * 一次瞬时失败（后端重启、代理闪断）拿不到快照，于是返回 `false`、**轮询永久停止**
 * —— 而后端 runner 还在跑（它不依赖这个页面）。界面的后果是：停在旧快照、按钮被
 * `running` 永久禁用、页面没有任何刷新入口，用户只能重启进程。这正是
 * `slices/crawl/runner.py` 立意要消灭的那种「静默失败」。
 *
 * ⚠️ 判停**不能**只看 `snapshot` 是否为 `undefined`：react-query 在请求失败时
 * **保留上一次的数据**（`error` reducer 是 `{...state, error, …}`）。于是
 * 「上一次成功时是空闲态」+ 之后一直失败，`snapshot` 会恒为 `{running: false}`，
 * 拿它判停就退化成同一个「一击不中永久停摆」—— 只是触发形态从「data 为 undefined」
 * 换成了「**陈旧**的 `running: false`」。所以还必须要求 `failures === 0`：
 * 那才说明这个快照是**现在**拿到了结论，而不是上次的残影。
 *
 * `failures` = **连续**失败次数。由 `useCrawlStatus` 的 queryFn 自己维护
 * （为什么不用 react-query 自带的计数器，见那里的注释）。拿不到明确结论时按指数
 * 退避、封顶在 `CRAWL_POLL_MAX_MS`：既保留「后端回来界面自己恢复」，又不会在后端
 * 长时间不可达时**每秒**硬打一遍（原实现没有上限，一个开着的页面会一直给已经倒下
 * 的后端施压）。
 */
export function crawlPollInterval(
  snapshot: { running: boolean } | undefined,
  failures = 0,
): number | false {
  if (failures === 0 && snapshot !== undefined && !snapshot.running) {
    return false;
  }
  return backoff(POLL_MS, failures);
}

/** 指数退避：0 次失败 = 基础间隔；之后翻倍，封顶。 */
function backoff(base: number, failures: number): number {
  return failures <= 0 ? base : Math.min(base * 2 ** failures, CRAWL_POLL_MAX_MS);
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
  // 连续失败次数：后端不可达时据此退避（见 crawlPollInterval）。
  //
  // 为什么自己数而不用 react-query 的计数器：v5 里 `fetchFailureCount` 在**每次
  // fetch 开始**时就被 `fetchState()` 归零、失败时只加回 1（源码 query.js 的
  // `"fetch"` / `"error"` reducer），所以每轮失败后它恒为 1 —— 拿它算退避只会得到
  // 一个**恒定**的 2 秒（实测：0/2/4/6/8… 秒，不是指数）。`errorUpdateCount` 只增
  // 不减（成功也不清零），同样不能用。计数器放在 queryFn 里维护：每次尝试恰好更新一次。
  const failures = useRef(0);
  const query = useQuery({
    queryKey: ["crawl", "status"],
    queryFn: async () => {
      try {
        const snapshot = await fetchStatus();
        failures.current = 0;
        return snapshot;
      } catch (error) {
        failures.current += 1;
        throw error;
      }
    },
    refetchInterval: (query) => crawlPollInterval(query.state.data, failures.current),
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
      const { data, error, response } = await api.POST("/api/crawl");
      if (error || !data) {
        // 「上一轮还在跑」这类原因后端写在 detail 里，走漏斗取出来。
        throw new Error(apiErrorMessage(error, "启动抓取失败", response));
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
