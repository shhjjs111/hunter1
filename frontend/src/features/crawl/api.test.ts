import { describe, expect, it } from "vitest";

import { crawlPollInterval } from "./api";

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
});
