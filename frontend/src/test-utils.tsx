import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import type { MobileChrome } from "./app/mobileChrome";
import { MobileChromeContext } from "./app/mobileChrome";

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export const JOBS = [
  {
    id: "j1",
    title: "AI产品经理",
    company: "字节跳动",
    city: "北京",
    match_score: 88,
    source: "实习僧",
    capture_status: "complete" as const,
    detail_url: "https://example.com/jobs/1",
    last_seen_at: null,
  },
  {
    id: "j2",
    title: "大模型应用工程师",
    company: "月之暗面",
    city: "上海",
    match_score: null,
    source: "Boss直聘",
    capture_status: "failed" as const,
    detail_url: "javascript:alert(1)",
    last_seen_at: null,
  },
];

export const JOB_LIST = {
  items: JOBS,
  total: JOBS.length,
  page: 1,
  page_size: 20,
  has_next: false,
};

export const APPLICATIONS = [
  {
    id: "app1",
    job_id: "j1",
    company: "字节跳动",
    title: "AI产品经理",
    stage: "applied" as const,
    applied_at: "2026-10-05T00:00:00Z",
    updated_at: "2026-10-05T00:00:00Z",
    note: null as string | null,
  },
  {
    id: "app2",
    job_id: "j9",
    company: "月之暗面",
    title: "大模型应用工程师",
    stage: "interview" as const,
    applied_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-08T00:00:00Z",
    note: "二面准备",
  },
];

export const APPLICATION_LIST = {
  items: APPLICATIONS,
  total: APPLICATIONS.length,
  page: 1,
  page_size: 50,
  has_next: false,
  has_more: false,
};

export const CRAWL_IDLE = {
  running: false,
  started_at: "2026-10-18T00:00:00Z",
  finished_at: "2026-10-18T00:01:00Z",
  error: null,
  sites: [
    { label: "实习僧", status: "ok", fetched: 12, created: 3, updated: 1, error: null },
    { label: "Boss直聘", status: "failed", fetched: 0, created: 0, updated: 0, error: "风控页" },
  ],
  total_fetched: 12,
  total_created: 3,
};

export const SETTINGS = {
  base_url: "https://api.example.com/v1",
  model: "gpt-test",
  masked_key: "sk-…1234",
  configured: true,
  broken: false,
};

export const PROFILE_VIEW = {
  profile: { keywords: ["AI产品经理"], directions: ["Agent 产品"], summary: "五年产品经验" },
  warning: null,
};

export const CONVERSATIONS = {
  items: [{ id: "c1", title: "旧会话", updated_at: "2026-10-18T00:00:00Z" }],
  total: 1,
  page: 1,
  page_size: 50,
  has_next: false,
};

export const SCORE = {
  job_id: "j2",
  score: 91,
  summary: "匹配度高",
  advantages: "产品经验对口",
  gaps: "缺少大模型落地案例",
  model: "gpt-test",
  prompt_version: "v1",
  scored_at: "2026-10-19T00:00:00Z",
};

export function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

export function makeChrome(overrides: Partial<MobileChrome> = {}): MobileChrome {
  return {
    animateTo: vi.fn(),
    showToast: vi.fn(),
    setOverlayOpen: vi.fn(),
    drawerOpen: false,
    setDrawerOpen: vi.fn(),
    setKeyboardOpen: vi.fn(),
    ...overrides,
  };
}

export function renderWithChrome(ui: ReactElement, chrome = makeChrome()) {
  const client = makeClient();
  return {
    chrome,
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MobileChromeContext.Provider value={chrome}>{ui}</MobileChromeContext.Provider>
      </QueryClientProvider>,
    ),
  };
}

export function renderRoutes(routes: Parameters<typeof createMemoryRouter>[0], path: string) {
  const client = makeClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return {
    client,
    router,
    ...render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

type ApiHandler = (request: Request, url: URL) => Response | undefined;

/** 假后端：覆盖手机端五个页面会打到的所有端点，可逐条覆盖。 */
export function stubApi(handler?: ApiHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(request.url);
    const custom = handler?.(request, url);
    if (custom) {
      return custom;
    }
    const path = url.pathname;
    const method = request.method;

    if (path === "/api/jobs" && method === "GET") {
      return jsonResponse(JOB_LIST);
    }
    if (path.startsWith("/api/jobs/") && method === "GET") {
      const job = JOBS.find((item) => path.endsWith(item.id)) ?? JOBS[0];
      return jsonResponse({ ...job, jd_raw: "负责大模型产品规划与落地。" });
    }
    if (path === "/api/crawl/status") {
      return jsonResponse(CRAWL_IDLE);
    }
    if (path === "/api/crawl" && method === "POST") {
      return jsonResponse({ started: true });
    }
    if (path === "/api/applications" && method === "GET") {
      return jsonResponse(APPLICATION_LIST);
    }
    if (path === "/api/applications" && method === "POST") {
      return jsonResponse({ application_id: "app9" }, 201);
    }
    if (path.startsWith("/api/applications/") && method === "POST") {
      return jsonResponse({ id: path.split("/")[3], stage: "interview" });
    }
    if (path.startsWith("/api/applications/") && method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (path === "/api/settings" && method === "GET") {
      return jsonResponse(SETTINGS);
    }
    if (path === "/api/settings" && method === "PUT") {
      return jsonResponse(SETTINGS);
    }
    if (path === "/api/settings/test") {
      return jsonResponse({ ok: true, message: "连接正常 · 312ms" });
    }
    if (path === "/api/scoring/profile" && (method === "GET" || method === "PUT")) {
      return jsonResponse(PROFILE_VIEW);
    }
    if (path.startsWith("/api/scoring/") && method === "POST") {
      return jsonResponse(SCORE);
    }
    if (path.startsWith("/api/scoring/") && method === "GET") {
      return jsonResponse({ detail: "还没有评分" }, 404);
    }
    if (path === "/api/assistant/conversations") {
      return jsonResponse(CONVERSATIONS);
    }
    if (path.startsWith("/api/assistant/conversations/")) {
      return jsonResponse([{ role: "user", content: "你好" }, { role: "assistant", content: "在的" }]);
    }
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** 让「减少动态效果」和手机宽度查询结果可控（jsdom 默认没有 matchMedia）。 */
export function stubMedia({ reduced = true, mobile = true } = {}) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduced : query.includes("max-width") ? mobile : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}
