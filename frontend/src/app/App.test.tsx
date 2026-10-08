import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { appRoutes } from "./App";

/**
 * 整体渲染验收 —— 挂载**真实的 App**（真路由、真布局、真 Query）
 * 并逐一访问路由，断言每页渲染出可见内容。
 *
 * 与各 feature 的组件测试的区别：那些测「组件自己的行为」，
 * 这里测「组装起来的界面能不能打开」—— 路由漏挂、布局崩、Provider 缺失
 * 这类问题只有整体渲染才暴露。
 *
 * 外部世界（后端）用 stub 的 fetch 顶替；中间层（路由/Provider/布局）全真。
 */

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** 按路径分派的假后端 —— 每个端点给最小可用响应。 */
function stubBackend() {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.includes("/api/jobs")) {
      return jsonResponse({
        items: [
          {
            id: "j1",
            title: "渲染验收岗",
            company: "验收公司",
            city: "北京",
            match_score: 77,
            source: "实习僧",
            capture_status: "complete",
            detail_url: "https://x/1",
            last_seen_at: null,
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
        has_next: false,
      });
    }
    if (url.includes("/api/crawl/status")) {
      return jsonResponse({
        running: false,
        started_at: null,
        finished_at: null,
        error: null,
        sites: [],
        total_fetched: 0,
        total_created: 0,
      });
    }
    if (url.includes("/api/applications")) {
      return jsonResponse({ items: [] });
    }
    if (url.includes("/api/settings")) {
      return jsonResponse({
        base_url: "https://api.example.com/v1",
        model: "m",
        masked_key: "sk-…1234",
        configured: true,
      });
    }
    if (url.includes("/api/assistant/conversations")) {
      return jsonResponse([]);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", mock);
}

function renderAt(path: string) {
  // 用 memory router 挂**同一份路由表**：真实的路由匹配、布局、Provider 都在，
  // 只有 URL 来源不同（内存而非浏览器地址栏）。
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("App 整体渲染", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("岗位库页：导航 + 岗位渲染", async () => {
    stubBackend();
    renderAt("/");
    expect(await screen.findByText("渲染验收岗")).toBeTruthy();
    expect(screen.getByText("验收公司")).toBeTruthy();
    expect(screen.getByText("77")).toBeTruthy();
  });

  it("左侧导航五项齐全", async () => {
    stubBackend();
    renderAt("/");
    // 「岗位库」既是导航项也是页面标题 —— 用 role 精确取导航链接
    const nav = await screen.findByRole("navigation");
    for (const label of ["岗位库", "抓取", "投递记录", "求职助手", "配置"]) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }
  });

  it("当前页面的导航项带 aria-current，其余项没有", async () => {
    // 屏幕阅读器靠 `aria-current` 才知道「你在哪一页」。react-router 的 `NavLink`
    // 默认会加它 —— 但这是**库的默认**，换回 `<Link>`、或哪天手写导航就会静默丢掉。
    stubBackend();
    renderAt("/");
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "岗位库" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(within(nav).getByRole("link", { name: "抓取" }).getAttribute("aria-current")).toBeNull();
  });

  it("抓取页渲染", async () => {
    stubBackend();
    renderAt("/crawl");
    expect(await screen.findByText("开始抓取")).toBeTruthy();
  });

  it("投递页渲染空态", async () => {
    stubBackend();
    renderAt("/applications");
    expect(await screen.findByText(/还没有投递记录/)).toBeTruthy();
  });

  it("配置页渲染且密钥只显示掩码", async () => {
    stubBackend();
    renderAt("/settings");
    expect(await screen.findByText(/sk-…1234/)).toBeTruthy();
    // 密钥输入框是空的（完整 key 只在掩码里出现，不进输入框）
    const keyInput = document.querySelector<HTMLInputElement>('input[id="api_key"]');
    expect(keyInput).not.toBeNull();
    expect(keyInput?.value).toBe("");
    expect(keyInput?.type).toBe("password");
  });

  it("助手页渲染输入区", async () => {
    stubBackend();
    renderAt("/assistant");
    expect(await screen.findByText(/只读：它查岗位与投递/)).toBeTruthy();
    expect(screen.getByPlaceholderText("有什么想问的？")).toBeTruthy();
  });

  it("岗位库页只有一个 main 地标（页面内容不该再嵌一层 main）", async () => {
    // 审查 P2-16：JobsPage 曾在 AppLayout 的 <main> 里再渲染一个 <main> —— 两个
    // 主地标，屏幕阅读器的「跳到主要内容」会给出两个同名项。其余四页都是 <div>。
    stubBackend();
    renderAt("/");
    await screen.findByText("渲染验收岗");
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });

  it("未注册的路径渲染兜底页而不是空白", async () => {
    stubBackend();
    renderAt("/no-such-page");
    expect(await screen.findByText("页面不存在")).toBeTruthy();
    // 导航仍在（用户能自己走回去），且有一条回岗位库的链接
    expect(screen.getByRole("navigation")).toBeTruthy();
    expect(screen.getByRole("link", { name: "回到岗位库" })).toBeTruthy();
  });

  it("后端不可用时页面显示错误而不是白屏", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "boom" }, 500)),
    );
    renderAt("/");
    expect(await screen.findByText(/加载岗位失败/)).toBeTruthy();
  });
});
