import "fake-indexeddb/auto";

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import {
  getDB,
  getMeta,
  newId,
  resetDBForTests,
  saveLLM,
  saveProfile,
  type JobRecord,
} from "./db";
import {
  crawlSite,
  httpFailureSummary,
  parseDetailPage,
  parseListPage,
  pauseMs,
  SITES,
  type SiteSpec,
} from "./crawlers";
import {
  installLocalApi,
  maybeAutoCrawl,
  resetLocalApiForTests,
  safeErrorMessage,
  shouldAutoCrawl,
} from "./localApi";

type ExternalHandler = (url: string, init?: RequestInit) => Response | Promise<Response>;

const realFetch = globalThis.fetch;
let externalHandler: ExternalHandler = () => new Response("not found", { status: 404 });

function mockFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = new URL(input.toString());
  if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
    throw new Error(`API 请求不应走外部 mock：${url.pathname}`);
  }
  return Promise.resolve(externalHandler(url.toString(), init));
}

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`${location.origin}${path}`, init);
  const text = await response.text();
  return {
    status: response.status,
    json: text ? (JSON.parse(text) as Record<string, unknown>) : null,
    text,
  };
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: newId(),
    title: "后端实习生",
    company: "示例公司",
    city: "上海",
    source: "实习僧",
    detailUrl: `https://example.invalid/${newId()}`,
    jdRaw: "岗位职责：写代码",
    captureStatus: "complete",
    matchScore: null,
    lastSeenAt: "2026-10-19T10:00:00.000Z",
    scoreSummary: null,
    scoreAdvantages: null,
    scoreGaps: null,
    scoreModel: null,
    scorePromptVersion: null,
    scoredAt: null,
    ...overrides,
  };
}

beforeAll(() => {
  globalThis.fetch = mockFetch as typeof fetch;
  installLocalApi();
});

beforeEach(async () => {
  await resetLocalApiForTests();
  await resetDBForTests();
  externalHandler = () => new Response("not found", { status: 404 });
  installLocalApi();
});

afterAll(() => {
  globalThis.fetch = realFetch;
});

describe("本地设置与画像", () => {
  it("外部网站的同名 API 路径不会读写手机数据库", async () => {
    externalHandler = () => new Response("external", { status: 202 });
    const response = await fetch("https://example.com/api/scoring/profile", {
      ...jsonBody({ keywords: ["外部请求"] }), method: "PUT",
    });
    expect(response.status).toBe(202);
    expect(await response.text()).toBe("external");
    expect((await api("/api/scoring/profile")).json?.profile).toBeNull();
  });
  it("拒绝空白与超限画像，避免保存后同步被电脑端拒绝", async () => {
    for (const body of [
      { keywords: ["  "], directions: [], summary: "" },
      { keywords: Array.from({ length: 51 }, () => "AI") },
      { keywords: ["x".repeat(101)] },
      { directions: Array.from({ length: 21 }, () => "AI") },
      { summary: "x".repeat(2001) },
    ]) {
      const result = await api("/api/scoring/profile", { ...jsonBody(body), method: "PUT" });
      expect(result.status).toBe(422);
    }
    expect((await api("/api/scoring/profile")).json?.profile).toBeNull();
  });

  it("保存配置后只回显掩码，测试连接成功", async () => {
    expect((await api("/api/settings")).json).toBeNull();

    const saved = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_url: "https://api.example.com/v1",
        model: "demo",
        api_key: "secret-key-123456",
      }),
    });
    expect(saved.status).toBe(200);
    expect(saved.json?.configured).toBe(true);
    expect(String(saved.json?.masked_key)).not.toContain("secret-key");

    externalHandler = () =>
      new Response(
        JSON.stringify({ model: "demo", choices: [{ message: { content: "OK" } }] }),
        { status: 200 },
      );
    const probe = await api("/api/settings/test", jsonBody({
      base_url: "https://api.example.com/v1",
      model: "demo",
      api_key: "",
    }));
    expect(probe.json).toMatchObject({ ok: true });
  });

  it("画像至少要有信号，保存后可读回", async () => {
    const empty = await api("/api/scoring/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keywords: [], directions: [], summary: "" }),
    });
    expect(empty.status).toBe(422);

    const saved = await api("/api/scoring/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keywords: ["后端"], directions: ["互联网"], summary: "三年经验" }),
    });
    expect(saved.status).toBe(200);
    const read = await api("/api/scoring/profile");
    expect(read.json?.profile).toMatchObject({
      keywords: ["后端"],
      directions: ["互联网"],
      summary: "三年经验",
    });
  });
});

describe("本地岗位与投递", () => {
  it("拒绝非法阶段与超长备注，并保留原投递", async () => {
    const db = await getDB();
    const target = job();
    await db.put("jobs", target);
    const created = await api("/api/applications", jsonBody({ job_id: target.id }));
    const id = created.json?.application_id as string;
    for (const body of [{ stage: "invalid" }, { stage: "interview", note: "x".repeat(2001) }]) {
      expect((await api(`/api/applications/${id}/stage`, jsonBody(body))).status).toBe(422);
    }
    expect(await db.get("applications", id)).toMatchObject({ stage: "applied", note: null });
  });

  it("并发记录同一岗位只创建一条投递", async () => {
    const db = await getDB();
    const target = job();
    await db.put("jobs", target);

    const responses = await Promise.all(
      Array.from({ length: 4 }, () =>
        api("/api/applications", jsonBody({ job_id: target.id })),
      ),
    );

    expect(new Set(responses.map((response) => response.json?.application_id)).size).toBe(1);
    expect(await db.getAllFromIndex("applications", "by-job", target.id)).toHaveLength(1);
  });

  it("列表、搜索、分页、详情与前缀歧义", async () => {
    const db = await getDB();
    const first = job({ id: "abc111", title: "后端实习生", lastSeenAt: "2026-10-19T11:00:00.000Z" });
    const second = job({ id: "abc222", title: "前端实习生", lastSeenAt: "2026-10-19T10:00:00.000Z" });
    await db.put("jobs", first);
    await db.put("jobs", second);

    const all = await api("/api/jobs");
    expect(all.json?.total).toBe(2);
    expect((all.json?.items as JobRecord[])[0].title).toBe("后端实习生");

    const search = await api("/api/jobs?q=前端");
    expect(search.json?.total).toBe(1);

    const page = await api("/api/jobs?page_size=1");
    expect(page.json?.has_next).toBe(true);

    const detail = await api("/api/jobs/abc111");
    expect(detail.json?.jd_raw).toContain("写代码");

    const prefix = await api("/api/jobs/abc111");
    expect(prefix.status).toBe(200);
    const ambiguous = await api("/api/jobs/abc");
    expect(ambiguous.status).toBe(409);
    const missing = await api("/api/jobs/nope");
    expect(missing.status).toBe(404);
  });

  it("记录投递幂等，阶段更新与删除", async () => {
    const db = await getDB();
    const target = job();
    await db.put("jobs", target);

    const created = await api("/api/applications", jsonBody({ job_id: target.id }));
    expect(created.status).toBe(201);
    const applicationId = created.json?.application_id as string;

    const again = await api("/api/applications", jsonBody({ job_id: target.id }));
    expect(again.status).toBe(200);
    expect(again.json?.application_id).toBe(applicationId);

    const list = await api("/api/applications");
    expect(list.json?.total).toBe(1);

    const staged = await api(`/api/applications/${applicationId}/stage`, jsonBody({
      stage: "interview",
      note: "约了下周",
    }));
    expect(staged.json).toMatchObject({ application_id: applicationId, stage: "interview" });

    const removed = await api(`/api/applications/${applicationId}`, { method: "DELETE" });
    expect(removed.status).toBe(204);
    expect((await api("/api/applications")).json?.total).toBe(0);
  });
});

describe("本地评分与助手", () => {
  it("中止助手立即结束请求，模型晚到的回复不会保存", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });
    const started = deferred<void>();
    const model = deferred<Response>();
    externalHandler = () => { started.resolve(); return model.promise; };
    const controller = new AbortController();
    const pending = fetch(`${location.origin}/api/assistant/stream`, {
      ...jsonBody({ message: "问题", conversation_id: "" }),
      signal: controller.signal,
    });
    await started.promise;
    controller.abort();
    model.resolve(new Response(JSON.stringify({ choices: [{ message: { content: "晚到回答" } }] })));

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    const db = await getDB();
    expect(await db.getAll("messages")).toHaveLength(0);
    expect(await db.getAll("conversations")).toHaveLength(0);
  });

  it("模型失败不会留下半轮用户消息", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });
    externalHandler = () => new Response("failed", { status: 503 });
    const response = await fetch(`${location.origin}/api/assistant/stream`, jsonBody({ message: "问题" }));
    expect(await response.text()).toContain('"type":"error"');
    const db = await getDB();
    expect(await db.getAll("messages")).toHaveLength(0);
    expect(await db.getAll("conversations")).toHaveLength(0);
  });

  it("等待模型期间更新的岗位事实不会被评分写回覆盖", async () => {
    const db = await getDB();
    const target = job();
    await db.put("jobs", target);
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });
    await saveProfile({ keywords: ["后端"], directions: [], summary: "" });
    externalHandler = async () => {
      await db.put("jobs", { ...target, title: "最新岗位标题", jdRaw: "最新岗位要求" });
      return new Response(JSON.stringify({
        model: "demo",
        choices: [{ message: { content: '{"score":88}' } }],
      }));
    };

    const result = await api(`/api/scoring/${target.id}`, { method: "POST" });

    expect(result.status).toBe(200);
    expect(await db.get("jobs", target.id)).toMatchObject({
      title: "最新岗位标题", jdRaw: "最新岗位要求", matchScore: 88,
    });
  });

  it("评分写回岗位，未配置画像时给出可读原因", async () => {
    const db = await getDB();
    const target = job();
    await db.put("jobs", target);
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });

    const missing = await api(`/api/scoring/${target.id}`, { method: "POST" });
    expect(missing.status).toBe(422);
    expect(String(missing.json?.detail)).toContain("画像");

    await saveProfile({ keywords: ["后端"], directions: [], summary: "" });
    externalHandler = () =>
      new Response(
        JSON.stringify({
          model: "demo",
          choices: [
            {
              message: {
                content: JSON.stringify({
                  score: 88,
                  advantages: "技术栈匹配",
                  gaps: "缺少实习",
                  summary: "值得投递",
                }),
              },
            },
          ],
        }),
        { status: 200 },
      );

    const scored = await api(`/api/scoring/${target.id}`, { method: "POST" });
    expect(scored.status).toBe(200);
    expect(scored.json).toMatchObject({ score: 88, summary: "值得投递" });

    const read = await api(`/api/scoring/${target.id}`);
    expect(read.json?.score).toBe(88);
    const stored = await db.get("jobs", target.id);
    expect(stored?.matchScore).toBe(88);
  });

  it("助手把回答落进会话并返回 SSE", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });
    externalHandler = () =>
      new Response(
        JSON.stringify({ model: "demo", choices: [{ message: { content: "你好，先看岗位库。" } }] }),
        { status: 200 },
      );

    const response = await fetch(`${location.origin}/api/assistant/stream`, jsonBody({
      message: "我该怎么开始？",
      conversation_id: "",
    }));
    const text = await response.text();
    expect(text).toContain('"type":"text"');
    expect(text).toContain('"type":"done"');

    const conversations = await api("/api/assistant/conversations");
    expect(conversations.json?.total).toBe(1);
    const id = (conversations.json?.items as Array<{ id: string }>)[0].id;
    const messages = await api(`/api/assistant/conversations/${id}`);
    expect(messages.json).toHaveLength(2);
  });
});

describe("自动补抓", () => {
  it("24 小时内不重复抓，缺失或损坏时间戳会重试", () => {
    const now = Date.parse("2026-10-19T12:00:00.000Z");
    expect(shouldAutoCrawl(null, now)).toBe(true);
    expect(shouldAutoCrawl("bad", now)).toBe(true);
    expect(shouldAutoCrawl(new Date(now - 23 * 3600_000).toISOString(), now)).toBe(false);
    expect(shouldAutoCrawl(new Date(now - 25 * 3600_000).toISOString(), now)).toBe(true);
    // 上一轮失败后 1 小时内不再因为切回前台重试。
    expect(
      shouldAutoCrawl(null, now, new Date(now - 10 * 60_000).toISOString()),
    ).toBe(false);
    expect(
      shouldAutoCrawl(null, now, new Date(now - 61 * 60_000).toISOString()),
    ).toBe(true);
  });

  it("首次打开会启动一轮并记录成功时间", async () => {
    externalHandler = (url) => {
      if (url.includes("shixiseng.com/interns")) {
        return new Response(
          '<div class="intern-wrap"><a class="title font" href="/intern/1">后端实习生</a><span class="city">上海</span><div class="f-r"><span class="title">示例公司</span></div></div>',
          { status: 200 },
        );
      }
      if (url.includes("shixiseng.com/intern/1")) {
        return new Response('<div class="con-job">职责</div>', { status: 200 });
      }
      return new Response("<html><body></body></html>", { status: 200 });
    };

    expect(await maybeAutoCrawl()).toBe(true);
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      const status = (await api("/api/crawl/status")).json;
      if (status?.running === false) {
        break;
      }
    }
    expect((await getMeta())?.lastCrawlAt).toBeTruthy();
    expect(await maybeAutoCrawl()).toBe(false);
  });
});

describe("抓取解析与本地抓取", () => {
  it("长 JD 连同截断提示不超过同步契约上限", () => {
    const jd = parseDetailPage(`<div class="jd">${"长".repeat(200_001)}</div>`, ".jd");
    expect(jd).toHaveLength(200_000);
    expect(jd?.endsWith("（岗位描述过长，已截断）")).toBe(true);
  });

  it("解析列表与详情选择器", () => {
    const spec = SITES[0];
    const html = `
      <div class="intern-wrap">
        <a class="title font" href="/intern/1">后端实习生</a>
        <span class="city">上海</span>
        <div class="f-r"><span class="title">示例公司</span></div>
      </div>`;
    const jobs = parseListPage(html, spec, "https://www.shixiseng.com/interns?page=1");
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "后端实习生",
      company: "示例公司",
      city: "上海",
      detailUrl: "https://www.shixiseng.com/intern/1",
    });
    expect(
      parseDetailPage(
        '<div class="con-job">职责 A</div><div class="con-job">要求 B</div>',
        ".con-job",
      ),
    ).toBe("职责 A\n要求 B");
  });

  it("没有公司/城市选择器的站点也能解析", () => {
    const spec = SITES.find((site) => site.key === "yingjiesheng");
    if (!spec) {
      throw new Error("missing yingjiesheng spec");
    }
    const jobs = parseListPage(
      '<div class="enterprise-list-item"><a href="/campaign/1"><span class="enterprise-list-title">某项目</span></a></div>',
      spec,
      "https://www.yingjiesheng.com/",
    );
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ title: "某项目", company: "应届生", city: null });
  });

  it("万行列表选择器可用，但匿名页固定为单页", () => {
    const spec = SITES.find((site) => site.key === "job910");
    if (!spec) {
      throw new Error("missing job910 spec");
    }
    expect(spec.maxPages).toBe(1);
    expect(SITES.find((site) => site.key === "shixiseng")?.maxPages).toBe(5);
    const jobs = parseListPage(
      `<div class="job-item">
         <div class="content-left-1-left"><a href="/jobs_view_1.html" class="left-name" title="图书管理员">图书管理员</a></div>
         <div class="content-left-1-right"><a href="/school_view_1.html" class="left-name" title="示例学校">示例学校</a></div>
         <ul class="content-left-2-left"><li>6K/月</li><li>广州-增城区</li><li>大专</li></ul>
       </div>`,
      spec,
      "https://www.job910.com/job/?page=1",
    );
    expect(jobs[0]).toMatchObject({
      title: "图书管理员",
      company: "示例学校",
      city: "广州-增城区",
      detailUrl: "https://www.job910.com/jobs_view_1.html",
    });
  });

  it("按 page 模板翻页，并在页面内容重复时停止", async () => {
    const spec: SiteSpec = {
      key: "example",
      label: "示例站",
      urlTemplate: "https://example.invalid/list?page={page}",
      itemSelector: ".item",
      titleSelector: ".title",
      linkSelector: ".title",
      maxPages: 5,
    };
    const calls: string[] = [];
    externalHandler = (url) => {
      calls.push(url);
      const page = new URL(url).searchParams.get("page");
      return new Response(
        `<div class="item"><a class="title" href="/job/${page}">岗位 ${page}</a></div>`,
        { status: 200 },
      );
    };
    const result = await crawlSite(
      spec,
      () => {},
      async (jobs) => ({ created: jobs.length, updated: 0 }),
      () => false,
    );
    expect(result.fetched).toBe(5);
    expect(calls).toHaveLength(5);
    expect(calls[4]).toContain("page=5");

    const repeated: string[] = [];
    externalHandler = (url) => {
      repeated.push(url);
      return new Response('<div class="item"><a class="title" href="/job/1">岗位一</a></div>', {
        status: 200,
      });
    };
    const single = await crawlSite(
      spec,
      () => {},
      async (jobs) => ({ created: jobs.length, updated: 0 }),
      () => false,
    );
    expect(single.fetched).toBe(1);
    expect(repeated).toHaveLength(2);
  });

  it("请求间隔带抖动且落在预期区间", () => {
    expect(pauseMs(900, () => 0)).toBe(900);
    expect(pauseMs(900, () => 0.999999)).toBe(1249);
  });

  it("抓取失败的风控页只给可读摘要，不直出 HTML", () => {
    const challenge =
      '<html><meta charset="utf-8" /><title></title><div></div></html>' +
      '<script> window.location.href ="/"; </script>';
    expect(httpFailureSummary(403, challenge)).toBe(
      "HTTP 403：站点拒绝访问（疑似风控），本轮跳过",
    );
    expect(httpFailureSummary(429, "")).toContain("站点拒绝访问");
    expect(httpFailureSummary(500, "<html><body>boom &nbsp; now</body></html>")).toBe(
      "HTTP 500：boom now",
    );
    expect(
      httpFailureSummary(
        500,
        '<script>alert(1)</script ><style>.x{}</style ><div>boom &nbsp; now</div>',
      ),
    ).toBe("HTTP 500：boom now");
    expect(httpFailureSummary(502, "")).toBe("HTTP 502：站点返回空响应");
  });

  it("回显错误只返回白名单文案，不回显原始消息", () => {
    expect(safeErrorMessage(new Error("第一行\n    at file.ts:1:2"))).toBe("操作失败，请重试");
    expect(safeErrorMessage("纯文本")).toBe("操作失败，请重试");
    expect(safeErrorMessage(new Error(""))).toBe("操作失败，请重试");
    expect(safeErrorMessage(new Error("HTTP 401：bad key"))).toContain("鉴权");
    expect(safeErrorMessage(new DOMException("late", "TimeoutError"))).toContain("请求超时");
  });

  it("已有 JD 的岗位不再重复抓详情", async () => {
    const spec: SiteSpec = {
      key: "example",
      label: "示例站",
      urlTemplate: "https://example.invalid/list",
      itemSelector: ".item",
      titleSelector: ".title",
      linkSelector: ".title",
      maxPages: 1,
      detailSelector: ".detail",
    };
    const calls: string[] = [];
    externalHandler = (url) => {
      calls.push(url);
      if (url.endsWith("/list")) {
        return new Response(
          '<div class="item"><a class="title" href="/job/1">岗位一</a></div>',
          { status: 200 },
        );
      }
      return new Response('<div class="detail">JD</div>', { status: 200 });
    };
    const result = await crawlSite(
      spec,
      () => {},
      async () => ({ created: 1, updated: 0 }),
      () => false,
    );
    expect(result.fetched).toBe(1);
    expect(calls.some((url) => url.endsWith("/job/1"))).toBe(false);
  });

  it("再次抓列表时保留已有 JD 与 complete 状态", async () => {
    const db = await getDB();
    const seeded = job({
      title: "后端实习生",
      company: "示例公司",
      city: "上海",
      source: "实习僧",
      detailUrl: "https://www.shixiseng.com/intern/1",
      jdRaw: "旧 JD",
      captureStatus: "complete",
    });
    await db.put("jobs", seeded);

    let detailCalls = 0;
    externalHandler = (url) => {
      if (url.includes("shixiseng.com/interns")) {
        return new Response(
          '<div class="intern-wrap"><a class="title font" href="/intern/1">后端实习生</a><span class="city">上海</span><div class="f-r"><span class="title">示例公司</span></div></div>',
          { status: 200 },
        );
      }
      if (url.includes("shixiseng.com/intern/1")) {
        detailCalls += 1;
        return new Response('<div class="con-job">新 JD</div>', { status: 200 });
      }
      return new Response("<html><body></body></html>", { status: 200 });
    };

    await api("/api/crawl", { method: "POST" });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      const status = (await api("/api/crawl/status")).json;
      if (status?.running === false) {
        break;
      }
    }

    const stored = (await db.getAll("jobs")).find(
      (item) => item.detailUrl === "https://www.shixiseng.com/intern/1",
    );
    expect(stored?.jdRaw).toBe("旧 JD");
    expect(stored?.captureStatus).toBe("complete");
    expect(detailCalls).toBe(0);
  });

  it("跑一轮抓取并落库", async () => {
    externalHandler = (url) => {
      if (url.includes("shixiseng.com/interns")) {
        return new Response(
          `<div class="intern-wrap">
             <a class="title font" href="/intern/1">后端实习生</a>
             <span class="city">上海</span>
             <div class="f-r"><span class="title">示例公司</span></div>
           </div>`,
          { status: 200 },
        );
      }
      if (url.includes("shixiseng.com/intern/1")) {
        return new Response('<div class="con-job">岗位职责：写代码</div>', { status: 200 });
      }
      return new Response("<html><body></body></html>", { status: 200 });
    };

    const started = await api("/api/crawl", { method: "POST" });
    expect(started.json).toMatchObject({ started: true });

    let status: Record<string, unknown> | null = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      status = (await api("/api/crawl/status")).json;
      if (status?.running === false && Array.isArray(status.sites) && status.sites.length > 0) {
        break;
      }
    }
    expect(status?.running).toBe(false);
    expect(status?.total_fetched).toBeGreaterThanOrEqual(1);
    const sites = status?.sites as Array<{ key: string; updated: number }>;
    expect(sites.find((site) => site.key === "shixiseng")?.updated).toBe(0);

    const jobs = await api("/api/jobs");
    expect(jobs.json?.total).toBe(1);
    expect((jobs.json?.items as Array<{ title: string }>)[0].title).toBe("后端实习生");
  });
});
