import { describe, expect, it } from "vitest";

import { CRAWL_POLL_MAX_MS, crawlPollInterval } from "./api";

const POLL_MS = 1000;

describe("crawlPollInterval", () => {
  it("抓取进行中 → 继续轮询", () => {
    expect(crawlPollInterval({ running: true })).toBe(POLL_MS);
  });

  it("后端明确说空闲 → 停止轮询", () => {
    expect(crawlPollInterval({ running: false })).toBe(false);
  });

  it("拿不到快照（首次请求未回 / 上一次失败）→ 继续轮询，不能就此停死", () => {
    // 原先写成 `data?.running ? POLL_MS : false`：一次瞬时失败（后端重启、代理
    // 闪断）就拿不到快照 → 返回 false → **轮询永久停止**，而后端 runner 还在跑。
    // 界面停在旧快照、按钮被永久禁用、页面没有刷新入口，只能重启进程。
    expect(crawlPollInterval(undefined)).toBe(POLL_MS);
  });

  it("后端不可达时按连续失败次数退避（不再每秒硬打）", () => {
    // 没有退避时，后端一旦倒下，一个开着的页面会以 1 秒的间隔**无限**重试——
    // 给已经起不来的后端持续施压。退避把「拿不到结论」的探测频率逐次减半。
    expect(crawlPollInterval(undefined, 0)).toBe(POLL_MS);
    expect(crawlPollInterval(undefined, 1)).toBe(POLL_MS * 2);
    expect(crawlPollInterval(undefined, 2)).toBe(POLL_MS * 4);
    expect(crawlPollInterval(undefined, 3)).toBe(POLL_MS * 8);
  });

  it("退避封顶（不会退成「永不重试」），成功一次后立刻回到基础间隔", () => {
    expect(crawlPollInterval(undefined, 99)).toBe(CRAWL_POLL_MAX_MS);
    // 抓取进行中且读取正常（failures 已归零）→ 必须回到 1 秒：进度要跟得上
    expect(crawlPollInterval({ running: true }, 0)).toBe(POLL_MS);
  });

  it("抓取进行中即使正在退避也不停止轮询（不重演「永久停摆」）", () => {
    const interval = crawlPollInterval({ running: true }, 5);
    expect(interval).not.toBe(false);
    expect(interval).toBeGreaterThan(POLL_MS);
  });

  it("陈旧的空闲快照不算「明确结论」—— 失败期间不会让轮询停死", () => {
    // react-query 在请求失败时**保留上一次的数据**，所以「上一次成功是空闲态」+
    // 之后一直失败，会让 snapshot 恒为 `{running: false}`。只按它判停，就退化成
    // 这个函数本来要消灭的那个「一击不中永久停摆」—— 只是触发形态从
    // 「data 为 undefined」换成了「陈旧的 running:false」。
    expect(crawlPollInterval({ running: false }, 1)).toBe(POLL_MS * 2);
    expect(crawlPollInterval({ running: false }, 3)).toBe(POLL_MS * 8);
    // 只有**没有失败**时，「空闲」才是当下的结论
    expect(crawlPollInterval({ running: false }, 0)).toBe(false);
  });
});
