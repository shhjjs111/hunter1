import {
  getDB,
  getMeta,
  getProfile,
  saveMeta,
  newId,
  saveProfile,
  type ApplicationRecord,
  type ApplicationStage,
  type ConversationRecord,
  type JobRecord,
  type MessageRecord,
  type ProfileRecord,
} from "./db";
import { crawlSite, SITES, type RawJob, type SiteProgress } from "./crawlers";
import {
  assistantReply,
  getSettings,
  saveSettings,
  scoreJob,
  testConnection,
} from "./llm";
import { publicResponseValue, safeErrorText } from "./http";

const APPLICATIONS_PAGE_SIZE = 50;
const CONVERSATIONS_PAGE_SIZE = 50;
const JOBS_PAGE_SIZE = 20;
const APPLICATION_STAGES: readonly ApplicationStage[] = [
  "applied", "written_test", "interview", "offer", "rejected", "withdrawn",
];

function json(data: unknown, status = 200): Response {
  // 本地 App 的用户就是数据所有者；`safeErrorMessage` 只回显首行并截断，
  // 不会返回 `error.stack`。
  return new Response(JSON.stringify(publicResponseValue(data)), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function failure(status: number, detail: string): Response {
  return json({ detail }, status);
}

/**
 * 只把错误的第一行回显给界面：用户可以据此修正配置，但看不到堆栈与文件路径。
 */
export function safeErrorMessage(error: unknown): string {
  return safeErrorText(error);
}

function now(): string {
  return new Date().toISOString();
}

function jobSummary(job: JobRecord) {
  return {
    id: job.id,
    title: job.title,
    company: job.company,
    city: job.city,
    match_score: job.matchScore,
    source: job.source,
    capture_status: job.captureStatus,
    detail_url: job.detailUrl,
    last_seen_at: job.lastSeenAt,
  };
}

function jobDetail(job: JobRecord) {
  return { ...jobSummary(job), jd_raw: job.jdRaw };
}

function applicationView(app: ApplicationRecord) {
  return {
    id: app.id,
    job_id: app.jobId,
    title: app.title,
    company: app.company,
    stage: app.stage,
    note: app.note,
    applied_at: app.appliedAt,
    updated_at: app.updatedAt,
  };
}

function scoreView(job: JobRecord) {
  if (job.matchScore === null) {
    return null;
  }
  return {
    job_id: job.id,
    score: job.matchScore,
    summary: job.scoreSummary,
    advantages: job.scoreAdvantages,
    gaps: job.scoreGaps,
    model: job.scoreModel,
    prompt_version: job.scorePromptVersion,
    scored_at: job.scoredAt,
  };
}

async function allJobs(): Promise<JobRecord[]> {
  const db = await getDB();
  return db.getAll("jobs");
}

async function findJob(ref: string): Promise<{ job: JobRecord | null; ambiguous: number }> {
  const jobs = await allJobs();
  const exact = jobs.find((job) => job.id === ref);
  if (exact) {
    return { job: exact, ambiguous: 0 };
  }
  const matches = jobs.filter((job) => job.id.startsWith(ref));
  if (matches.length === 1) {
    return { job: matches[0], ambiguous: 0 };
  }
  return { job: null, ambiguous: matches.length };
}

async function saveJobs(raw: RawJob[]): Promise<{ created: number; updated: number }> {
  const db = await getDB();
  const tx = db.transaction("jobs", "readwrite");
  const known = new Map((await tx.store.getAll()).map((job) => [job.detailUrl, job]));
  let created = 0;
  let updated = 0;
  const seen = new Set<string>();
  const writes: Array<Promise<unknown>> = [];

  for (const item of raw) {
    if (seen.has(item.detailUrl)) {
      continue;
    }
    seen.add(item.detailUrl);
    const existing = known.get(item.detailUrl);
    if (existing) {
      updated += 1;
      // 本轮没抓详情（因为已有 JD）时，列表页里的 `pending` 只是「待补」，
      // 不能把已经 `complete` 的状态降级 —— 那会让界面每天都显示一堆假待补。
      const captureStatus =
        item.jdRaw === null && existing.jdRaw ? existing.captureStatus : item.captureStatus;
      writes.push(
        tx.store.put({
          ...existing,
          title: item.title,
          company: item.company,
          city: item.city,
          jdRaw: item.jdRaw ?? existing.jdRaw,
          captureStatus,
          lastSeenAt: now(),
        }),
      );
    } else {
      created += 1;
      const job: JobRecord = {
        id: newId(),
        title: item.title,
        company: item.company,
        city: item.city,
        source: item.source,
        detailUrl: item.detailUrl,
        jdRaw: item.jdRaw,
        captureStatus: item.captureStatus,
        matchScore: null,
        lastSeenAt: now(),
        scoreSummary: null,
        scoreAdvantages: null,
        scoreGaps: null,
        scoreModel: null,
        scorePromptVersion: null,
        scoredAt: null,
      };
      writes.push(tx.store.add(job));
    }
  }

  await Promise.all(writes);
  await tx.done;
  return { created, updated };
}

let crawlState = {
  running: false,
  startedAt: null as string | null,
  finishedAt: null as string | null,
  error: null as string | null,
  totalFetched: 0,
  totalCreated: 0,
  sites: [] as SiteProgress[],
};

function crawlStatus() {
  return {
    running: crawlState.running,
    started_at: crawlState.startedAt,
    finished_at: crawlState.finishedAt,
    error: crawlState.error,
    total_fetched: crawlState.totalFetched,
    total_created: crawlState.totalCreated,
    sites: crawlState.sites.map((site) => ({
      key: site.key,
      label: site.label,
      status: site.status,
      fetched: site.fetched,
      created: site.created,
      updated: site.updated,
      error: site.error,
    })),
  };
}

async function runCrawl(): Promise<void> {
  const startedAt = now();
  crawlState = {
    running: true,
    startedAt,
    finishedAt: null,
    error: null,
    totalFetched: 0,
    totalCreated: 0,
    sites: SITES.map((site) => ({
      key: site.key,
      label: site.label,
      status: "pending",
      fetched: 0,
      created: 0,
      updated: 0,
      error: null,
    })),
  };
  try {
    // 记录「尝试」时间：失败时用它做冷却，避免用户每切回前台就重打一遍站点。
    const metaAtStart = await getMeta();
    await saveMeta({
      lastCrawlAt: metaAtStart?.lastCrawlAt ?? null,
      lastCrawlAttemptAt: startedAt,
      autoCrawl: metaAtStart?.autoCrawl ?? true,
    });

    const knownJd = new Map((await allJobs()).map((job) => [job.detailUrl, Boolean(job.jdRaw)]));
    const saveBatch = async (jobs: RawJob[]) => {
      const outcome = await saveJobs(jobs);
      // 同一轮后续站点/页面再看到同一 URL 时，不再重复抓详情。
      for (const job of jobs) {
        if (job.jdRaw) {
          knownJd.set(job.detailUrl, true);
        }
      }
      return outcome;
    };
    for (const site of SITES) {
      const result = await crawlSite(
        site,
        (patch) => {
          const current = crawlState.sites.find((item) => item.key === site.key);
          if (current) {
            Object.assign(current, patch);
            crawlState.totalFetched = crawlState.sites.reduce((sum, item) => sum + item.fetched, 0);
            crawlState.totalCreated = crawlState.sites.reduce((sum, item) => sum + item.created, 0);
          }
        },
        saveBatch,
        (job) => !knownJd.get(job.detailUrl),
      );
      const current = crawlState.sites.find((item) => item.key === site.key);
      if (current) {
        Object.assign(current, result);
      }
    }
  } catch (error) {
    crawlState.error = safeErrorMessage(error);
  } finally {
    crawlState.totalFetched = crawlState.sites.reduce((sum, item) => sum + item.fetched, 0);
    crawlState.totalCreated = crawlState.sites.reduce((sum, item) => sum + item.created, 0);
    try {
      const meta = await getMeta();
      const succeeded = crawlState.sites.some((site) => site.status === "ok");
      await saveMeta({
        lastCrawlAt: succeeded ? now() : (meta?.lastCrawlAt ?? null),
        lastCrawlAttemptAt: meta?.lastCrawlAttemptAt ?? startedAt,
        autoCrawl: meta?.autoCrawl ?? true,
      });
    } catch {
      // 元数据写失败不应把已经完成的抓取结果标成失败。
    }
    // 先把元数据写完再宣告 idle：否则界面/测试看到 `running: false` 时，
    // `lastCrawlAt` 可能还没落库，下一次前台检查会立刻再跑一轮。
    crawlState.finishedAt = now();
    crawlState.running = false;
  }
}

const AUTO_CRAWL_INTERVAL_MS = 24 * 60 * 60 * 1000;
/** 一轮全部失败后的冷却：1 小时内不再因「切回前台」自动重试。 */
export const AUTO_CRAWL_RETRY_MS = 60 * 60 * 1000;

export function shouldAutoCrawl(
  lastCrawlAt: string | null,
  nowMs: number,
  lastAttemptAt: string | null = null,
): boolean {
  if (lastCrawlAt) {
    const last = Date.parse(lastCrawlAt);
    if (Number.isFinite(last) && nowMs - last < AUTO_CRAWL_INTERVAL_MS) {
      return false;
    }
  }
  if (lastAttemptAt) {
    const attempt = Date.parse(lastAttemptAt);
    if (Number.isFinite(attempt) && nowMs - attempt < AUTO_CRAWL_RETRY_MS) {
      return false;
    }
  }
  return true;
}

let autoCrawlStarting = false;

/**
 * 打开 App / 回到前台时调用：距离上次成功抓取超过 24 小时就自动补一轮。
 * 失败后 1 小时内不重复尝试；同一时刻只允许一个启动流程。
 */
export async function maybeAutoCrawl(): Promise<boolean> {
  if (crawlState.running || autoCrawlStarting) {
    return false;
  }
  autoCrawlStarting = true;
  try {
    const meta = await getMeta();
    if (meta?.autoCrawl === false) {
      return false;
    }
    if (
      !shouldAutoCrawl(
        meta?.lastCrawlAt ?? null,
        Date.now(),
        meta?.lastCrawlAttemptAt ?? null,
      )
    ) {
      return false;
    }
    void runCrawl();
    return true;
  } catch {
    return false;
  } finally {
    autoCrawlStarting = false;
  }
}

async function profileView() {
  const profile = await getProfile();
  return {
    profile: profile
      ? {
          keywords: profile.keywords,
          directions: profile.directions,
          summary: profile.summary,
        }
      : null,
    warning: null,
  };
}

async function saveProfileView(body: {
  keywords?: string[];
  directions?: string[];
  summary?: string;
}) {
  const normalizeItems = (value: unknown, limit: number): string[] => {
    if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
      throw new Error("画像条目必须是文本列表");
    }
    const items = value.map((item: string) => item.trim()).filter(Boolean);
    if (items.length > limit || items.some((item) => item.length > 100)) {
      throw new Error("画像超出长度上限");
    }
    return items;
  };
  const keywords = normalizeItems(body.keywords ?? [], 50);
  const directions = normalizeItems(body.directions ?? [], 20);
  const summary = (body.summary ?? "").trim();
  if (summary.length > 2000) {
    throw new Error("画像超出长度上限");
  }
  if (!keywords.length && !directions.length && !summary) {
    throw new Error("画像至少要有一项信号（关键词 / 方向 / 背景摘要）");
  }
  const record: Omit<ProfileRecord, "key"> = { keywords, directions, summary };
  await saveProfile(record);
  return { profile: record, warning: null };
}

async function conversationsPage(page: number, pageSize: number) {
  const db = await getDB();
  const all = await db.getAll("conversations");
  all.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const offset = (page - 1) * pageSize;
  const items = all.slice(offset, offset + pageSize);
  return {
    items: items.map((item) => ({ id: item.id, title: item.title, updated_at: item.updatedAt })),
    total: all.length,
    page,
    page_size: pageSize,
    has_next: offset + items.length < all.length,
  };
}

async function messagesFor(conversationId: string) {
  const db = await getDB();
  const messages = await db.getAllFromIndex("messages", "by-conversation", conversationId);
  messages.sort((a, b) =>
    (a.sequence ?? 0) - (b.sequence ?? 0) || a.createdAt.localeCompare(b.createdAt),
  );
  return messages.map((message) => ({ role: message.role, content: message.content }));
}

async function saveTurn(
  conversation: ConversationRecord,
  message: string,
  reply: string,
  signal: AbortSignal,
): Promise<void> {
  const db = await getDB();
  signal.throwIfAborted();
  const tx = db.transaction(["messages", "conversations"], "readwrite");
  const abort = () => tx.abort();
  signal.addEventListener("abort", abort, { once: true });
  try {
    const store = tx.objectStore("messages");
    const existing = await store.index("by-conversation").getAll(conversation.id);
    let sequence = existing.reduce((maximum, item) => Math.max(maximum, item.sequence ?? 0), 0);
    const createdAt = now();
    for (const [role, content] of [["user", message], ["assistant", reply]]) {
      const record: MessageRecord = {
        id: newId(), conversationId: conversation.id, role, content,
        createdAt, sequence: ++sequence,
      };
      await store.put(record);
    }
    await tx.objectStore("conversations").put({ ...conversation, updatedAt: createdAt });
    await tx.done;
  } catch (error) {
    try { tx.abort(); } catch { /* 已结束的事务无需再中止。 */ }
    await tx.done.catch(() => {});
    throw error;
  } finally {
    signal.removeEventListener("abort", abort);
  }
}

async function ensureConversation(id: string, title: string): Promise<ConversationRecord> {
  const db = await getDB();
  if (id) {
    const existing = await db.get("conversations", id);
    if (existing) {
      return existing;
    }
  }
  const conversation: ConversationRecord = {
    id: id || newId(),
    title: title.slice(0, 40) || "新对话",
    updatedAt: now(),
  };
  return conversation;
}

/** 原生 HTTP 没有取消接口；中止后立即交还 UI，并丢弃请求后来返回的结果。 */
function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    if (signal.aborted) {
      operation.catch(() => {});
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    operation.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

function sse(event: unknown): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

async function handleAssistantStream(request: Request): Promise<Response> {
  const body = (await request.json()) as { message?: string; conversation_id?: string };
  const message = (body.message ?? "").trim();
  if (!message) {
    return failure(422, "消息不能为空");
  }
  try {
    request.signal.throwIfAborted();
    const conversation = await ensureConversation(body.conversation_id ?? "", message);
    const history = await messagesFor(conversation.id);
    request.signal.throwIfAborted();
    const reply = await abortable(assistantReply(message, history), request.signal);
    request.signal.throwIfAborted();
    await saveTurn(conversation, message, reply, request.signal);
    const payload =
      sse({ type: "text", text: reply }) +
      sse({ type: "done", conversation_id: conversation.id, truncated: false, degraded: false });
    return new Response(payload, {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
    });
  } catch (error) {
    if (request.signal.aborted) {
      throw request.signal.reason;
    }
    const detail = safeErrorMessage(error);
    return new Response(sse({ type: "error", message: detail }), {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
    });
  }
}

async function route(request: Request, url: URL): Promise<Response | null> {
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/settings" && method === "GET") {
    return json(await getSettings());
  }
  if (path === "/api/settings" && method === "PUT") {
    try {
      return json(await saveSettings((await request.json()) as {
        base_url: string;
        model: string;
        api_key: string;
      }));
    } catch (error) {
      return failure(422, safeErrorMessage(error));
    }
  }
  if (path === "/api/settings/test" && method === "POST") {
    return json(await testConnection((await request.json()) as {
      base_url: string;
      model: string;
      api_key: string;
    }));
  }

  if (path === "/api/jobs" && method === "GET") {
    const keyword = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = Math.max(1, Number(url.searchParams.get("page_size") ?? JOBS_PAGE_SIZE));
    const jobs = await allJobs();
    const filtered = keyword
      ? jobs.filter((job) =>
          `${job.title} ${job.company} ${job.city ?? ""}`.toLowerCase().includes(keyword),
        )
      : jobs;
    filtered.sort((a, b) => b.lastSeenAt.localeCompare(a.lastSeenAt));
    const offset = (page - 1) * pageSize;
    const items = filtered.slice(offset, offset + pageSize);
    return json({
      items: items.map(jobSummary),
      total: filtered.length,
      page,
      page_size: pageSize,
      has_next: offset + items.length < filtered.length,
    });
  }
  const jobMatch = /^\/api\/jobs\/([^/]+)$/.exec(path);
  if (jobMatch && method === "GET") {
    const { job, ambiguous } = await findJob(decodeURIComponent(jobMatch[1]));
    if (ambiguous > 1) {
      return failure(409, `id 前缀至少有 ${ambiguous} 条匹配，请给更长的 id`);
    }
    return job ? json(jobDetail(job)) : failure(404, "岗位不存在");
  }

  if (path === "/api/applications" && method === "GET") {
    const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = Math.max(
      1,
      Number(url.searchParams.get("page_size") ?? APPLICATIONS_PAGE_SIZE),
    );
    const db = await getDB();
    const all = await db.getAll("applications");
    all.sort((a, b) => b.appliedAt.localeCompare(a.appliedAt));
    const offset = (page - 1) * pageSize;
    const items = all.slice(offset, offset + pageSize);
    return json({
      items: items.map(applicationView),
      total: all.length,
      page,
      page_size: pageSize,
      has_next: offset + items.length < all.length,
      has_more: offset + items.length < all.length,
    });
  }
  if (path === "/api/applications" && method === "POST") {
    const body = (await request.json()) as { job_id?: string };
    const ref = (body.job_id ?? "").trim();
    if (!ref) {
      return failure(422, "job_id 不能为空");
    }
    const { job, ambiguous } = await findJob(ref);
    if (ambiguous > 1) {
      return failure(409, `id 前缀至少有 ${ambiguous} 条匹配，请给更长的 id`);
    }
    if (!job) {
      return failure(404, `岗位不存在：${ref}`);
    }
    const db = await getDB();
    // 查重和写入必须共用写事务，否则快速连点/多个窗口会各建一条。
    const tx = db.transaction("applications", "readwrite");
    const existing = await tx.store.index("by-job").getAll(job.id);
    if (existing.length > 0) {
      await tx.done;
      return json({ application_id: existing[0].id });
    }
    const record: ApplicationRecord = {
      id: newId(),
      jobId: job.id,
      title: job.title,
      company: job.company,
      stage: "applied",
      note: null,
      appliedAt: now(),
      updatedAt: now(),
    };
    await tx.store.put(record);
    await tx.done;
    return json({ application_id: record.id }, 201);
  }
  const stageMatch = /^\/api\/applications\/([^/]+)\/stage$/.exec(path);
  if (stageMatch && method === "POST") {
    const id = decodeURIComponent(stageMatch[1]);
    const body = (await request.json()) as { stage?: ApplicationStage; note?: string | null };
    const stage = body.stage;
    if (!stage || !APPLICATION_STAGES.includes(stage)) {
      return failure(422, "请选择有效的投递阶段");
    }
    if (body.note != null && (typeof body.note !== "string" || body.note.length > 2000)) {
      return failure(422, "投递备注必须是最多 2000 字符的文本");
    }
    const db = await getDB();
    const tx = db.transaction("applications", "readwrite");
    const existing = await tx.store.get(id);
    if (!existing) {
      await tx.done;
      return failure(404, `投递不存在：${id}`);
    }
    const updated: ApplicationRecord = {
      ...existing,
      stage,
      note: body.note === undefined ? existing.note : body.note,
      updatedAt: now(),
    };
    await tx.store.put(updated);
    await tx.done;
    return json({ application_id: updated.id, stage: updated.stage });
  }
  const applicationMatch = /^\/api\/applications\/([^/]+)$/.exec(path);
  if (applicationMatch && method === "DELETE") {
    const id = decodeURIComponent(applicationMatch[1]);
    const db = await getDB();
    await db.delete("applications", id);
    return new Response(null, { status: 204 });
  }

  if (path === "/api/scoring/profile" && method === "GET") {
    return json(await profileView());
  }
  if (path === "/api/scoring/profile" && method === "PUT") {
    try {
      return json(await saveProfileView((await request.json()) as {
        keywords?: string[];
        directions?: string[];
        summary?: string;
      }));
    } catch (error) {
      return failure(422, safeErrorMessage(error));
    }
  }
  const scoreMatch = /^\/api\/scoring\/([^/]+)$/.exec(path);
  if (scoreMatch && method === "GET") {
    const { job, ambiguous } = await findJob(decodeURIComponent(scoreMatch[1]));
    if (ambiguous > 1) {
      return failure(409, `id 前缀至少有 ${ambiguous} 条匹配，请给更长的 id`);
    }
    if (!job) {
      return failure(404, "岗位不存在");
    }
    const view = scoreView(job);
    return view ? json(view) : failure(404, "还没有评分");
  }
  if (scoreMatch && method === "POST") {
    const { job, ambiguous } = await findJob(decodeURIComponent(scoreMatch[1]));
    if (ambiguous > 1) {
      return failure(409, `id 前缀至少有 ${ambiguous} 条匹配，请给更长的 id`);
    }
    if (!job) {
      return failure(404, "岗位不存在");
    }
    try {
      const result = await scoreJob(job);
      const db = await getDB();
      // 模型请求可能耗时很久；只给事务内重读的当前记录补评分，保留其间抓取/同步的新事实。
      const tx = db.transaction("jobs", "readwrite");
      const current = await tx.store.get(job.id);
      if (!current) {
        await tx.done;
        return failure(404, "岗位不存在");
      }
      const updated: JobRecord = {
        ...current,
        matchScore: result.score,
        scoreSummary: result.summary,
        scoreAdvantages: result.advantages,
        scoreGaps: result.gaps,
        scoreModel: result.model,
        scorePromptVersion: result.promptVersion,
        scoredAt: now(),
      };
      await tx.store.put(updated);
      await tx.done;
      return json(scoreView(updated));
    } catch (error) {
      return failure(422, safeErrorMessage(error));
    }
  }

  if (path === "/api/crawl/status" && method === "GET") {
    return json(crawlStatus());
  }
  if (path === "/api/crawl" && method === "POST") {
    if (crawlState.running) {
      return json({ started: false });
    }
    void runCrawl();
    return json({ started: true });
  }

  if (path === "/api/assistant/conversations" && method === "GET") {
    const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = Math.max(
      1,
      Number(url.searchParams.get("page_size") ?? CONVERSATIONS_PAGE_SIZE),
    );
    return json(await conversationsPage(page, pageSize));
  }
  const conversationMatch = /^\/api\/assistant\/conversations\/([^/]+)$/.exec(path);
  if (conversationMatch && method === "GET") {
    return json(await messagesFor(decodeURIComponent(conversationMatch[1])));
  }
  if (path === "/api/assistant/stream" && method === "POST") {
    return handleAssistantStream(request);
  }

  return null;
}

let installed = false;
let previousFetch: typeof globalThis.fetch | null = null;

export function installLocalApi(): void {
  if (installed) {
    return;
  }
  installed = true;
  previousFetch = globalThis.fetch.bind(globalThis);
  const originalFetch = previousFetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      return originalFetch(input, init);
    }
    const response = await route(request, url);
    return response ?? failure(404, `本地接口未实现：${request.method} ${url.pathname}`);
  };
}

export async function resetLocalApiForTests(): Promise<void> {
  if (previousFetch) {
    globalThis.fetch = previousFetch;
  }
  previousFetch = null;
  installed = false;
  autoCrawlStarting = false;
  crawlState = {
    running: false,
    startedAt: null,
    finishedAt: null,
    error: null,
    totalFetched: 0,
    totalCreated: 0,
    sites: [],
  };
}
