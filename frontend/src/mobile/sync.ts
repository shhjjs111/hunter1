import type { components } from "../shared/api/schema";
import {
  getDB,
  newId,
  type ApplicationRecord,
  type ApplicationStage,
  type JobRecord,
  type ProfileRecord,
  type SyncRecord,
} from "./db";
import { requestJson } from "./http";
import { isPrivateHost } from "./netPolicy";

export type SyncApplication = components["schemas"]["SyncApplication"];
export type SyncCounts = components["schemas"]["SyncCounts"];
export type SyncJob = components["schemas"]["SyncJob"];
export type SyncMergeResponse = components["schemas"]["SyncMergeResponse"];
export type SyncProfile = components["schemas"]["SyncProfile"];
export type SyncSnapshot = components["schemas"]["SyncSnapshot"];

export interface SyncConfig {
  desktopUrl: string;
  accessCode: string;
  lastSyncAt: string | null;
  lastResult: string | null;
}

export type SyncMode = "both" | "push" | "pull";

export interface SyncOutcome {
  local: SyncCounts;
  desktop: SyncCounts | null;
  message: string;
}

const EPOCH = "1970-01-01T00:00:00.000Z";
const TRACKING_PARAMS = new Set([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "from",
  "fromsource",
  "ctmid",
  "spm",
  "share_token",
  "ref",
]);

function emptyCounts(): SyncCounts {
  return {
    jobs_created: 0,
    jobs_updated: 0,
    jobs_kept: 0,
    applications_created: 0,
    applications_updated: 0,
    applications_kept: 0,
    applications_skipped: 0,
    profile_updated: false,
  };
}

function timeOf(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** `left` 是否严格晚于 `right`（缺失视为最旧）。 */
function isNewer(left: string | null | undefined, right: string | null | undefined): boolean {
  const a = timeOf(left);
  const b = timeOf(right);
  if (a === null) {
    return false;
  }
  if (b === null) {
    return true;
  }
  return a > b;
}

/**
 * 与后端 `domain.crawl.normalize_detail_url` 对齐的岗位 URL 归一化。
 * 跨设备真正稳定的身份是详情页地址，不是 UUID —— 两边各自抓同一岗位会生成
 * 不同 id。这里不处理 51job 中间页改写（手机站点适配器不产出那种链接）。
 */
export function normalizeSyncUrl(raw: string): string {
  const text = raw.trim();
  if (!text) {
    return "";
  }
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return "";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return "";
  }
  url.hostname = url.hostname.toLowerCase();
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMS.has(key.toLowerCase())) {
      url.searchParams.delete(key);
    }
  }
  const path = url.pathname.replace(/\/+$/, "");
  const fragment = url.hash;
  const keepFragment = fragment.startsWith("#/") || fragment.startsWith("#!/");
  return `${url.protocol}//${url.host}${path}${url.search}${keepFragment ? fragment : ""}`;
}

/** 用户填的电脑地址只取 origin；允许省略 scheme。 */
export function normalizeDesktopUrl(raw: string): string {
  const text = raw.trim();
  if (!text) {
    throw new Error("请填写电脑地址，例如 http://192.168.1.5:8000");
  }
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `http://${text}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    throw new Error("电脑地址不是合法 URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("电脑地址必须以 http:// 或 https:// 开头");
  }
  if (!url.hostname) {
    throw new Error("电脑地址缺少主机名");
  }
  if (url.protocol === "http:" && !isPrivateHost(url.hostname)) {
    throw new Error("电脑地址是公网 HTTP：访问码会明文过网，请改用 https:// 或局域网地址");
  }
  return url.origin;
}

async function getSyncRecord(): Promise<SyncRecord | null> {
  const db = await getDB();
  return ((await db.get("settings", "sync")) as SyncRecord | undefined) ?? null;
}

export async function loadSyncConfig(): Promise<SyncConfig> {
  const record = await getSyncRecord();
  return {
    desktopUrl: record?.desktopUrl ?? "",
    accessCode: record?.accessCode ?? "",
    lastSyncAt: record?.lastSyncAt ?? null,
    lastResult: record?.lastResult ?? null,
  };
}

export async function saveSyncConfig(patch: Partial<SyncConfig>): Promise<SyncConfig> {
  const current = await loadSyncConfig();
  const next: SyncConfig = { ...current, ...patch };
  const db = await getDB();
  await db.put("settings", { key: "sync", ...next } satisfies SyncRecord);
  return next;
}

function jobToWire(job: JobRecord): SyncJob {
  return {
    id: job.id,
    title: job.title,
    company: job.company,
    city: job.city,
    source: job.source,
    detail_url: job.detailUrl,
    jd_raw: job.jdRaw,
    capture_status: job.captureStatus,
    match_score: job.matchScore,
    score_summary: job.scoreSummary,
    score_advantages: job.scoreAdvantages,
    score_gaps: job.scoreGaps,
    score_model: job.scoreModel,
    score_prompt_version: job.scorePromptVersion,
    scored_at: job.scoredAt,
    first_seen_at: null,
    last_seen_at: job.lastSeenAt,
  };
}

function applicationToWire(app: ApplicationRecord, jobDetailUrl: string | null): SyncApplication {
  return {
    job_detail_url: jobDetailUrl,
    title: app.title,
    company: app.company,
    stage: app.stage,
    note: app.note,
    applied_at: app.appliedAt,
    updated_at: app.updatedAt,
  };
}

export async function buildLocalSnapshot(): Promise<SyncSnapshot> {
  const db = await getDB();
  // 同一份快照里的岗位、投递与画像必须来自同一个一致性视图；分开读会在
  // 并发删除/写入时出现「投递指向已不在快照里的岗位」。
  const tx = db.transaction(["jobs", "applications", "settings"], "readonly");
  const [jobs, applications, storedProfile] = await Promise.all([
    tx.objectStore("jobs").getAll(),
    tx.objectStore("applications").getAll(),
    tx.objectStore("settings").get("profile"),
  ]);
  await tx.done;
  const profile = storedProfile as ProfileRecord | undefined;
  const urls = new Map(jobs.map((job) => [job.id, job.detailUrl]));
  return {
    version: 1,
    exported_at: new Date().toISOString(),
    jobs: jobs.map(jobToWire),
    applications: applications.map((app) =>
      applicationToWire(app, urls.get(app.jobId) ?? null),
    ),
    profile: profile
      ? {
          keywords: profile.keywords,
          directions: profile.directions,
          summary: profile.summary,
          updated_at: profile.updatedAt ?? EPOCH,
        }
      : null,
  };
}

function copyScore(record: JobRecord, incoming: SyncJob): JobRecord {
  return {
    ...record,
    matchScore: incoming.match_score ?? null,
    scoreSummary: incoming.score_summary ?? null,
    scoreAdvantages: incoming.score_advantages ?? null,
    scoreGaps: incoming.score_gaps ?? null,
    scoreModel: incoming.score_model ?? null,
    scorePromptVersion: incoming.score_prompt_version ?? null,
    scoredAt: incoming.scored_at ?? null,
  };
}

function scoreIsNewer(existing: JobRecord, incoming: SyncJob): boolean {
  if (incoming.match_score === null || incoming.match_score === undefined) {
    return false;
  }
  return existing.matchScore === null || isNewer(incoming.scored_at, existing.scoredAt);
}

function sameJob(a: JobRecord, b: JobRecord): boolean {
  const fields: Array<keyof JobRecord> = [
    "title",
    "company",
    "city",
    "source",
    "detailUrl",
    "jdRaw",
    "captureStatus",
    "matchScore",
    "lastSeenAt",
    "scoreSummary",
    "scoreAdvantages",
    "scoreGaps",
    "scoreModel",
    "scorePromptVersion",
    "scoredAt",
  ];
  return fields.every((field) => a[field] === b[field]);
}

export async function mergeLocalSnapshot(snapshot: SyncSnapshot): Promise<SyncCounts> {
  if (snapshot.version !== 1) {
    throw new Error("同步数据版本不受支持");
  }
  const db = await getDB();
  const counts = emptyCounts();
  // 读、算、写必须在同一个 IndexedDB 事务里：否则中途失败会留下半份岗位 /
  // 投递 / 画像，下一次同步看到的数据取决于上次失败停在哪一步。
  const tx = db.transaction(["jobs", "applications", "settings"], "readwrite");
  const jobsStore = tx.objectStore("jobs");
  const applicationsStore = tx.objectStore("applications");
  const settingsStore = tx.objectStore("settings");
  const writes: Array<Promise<unknown>> = [];
  try {
    const [jobs, applications, storedProfile] = await Promise.all([
      jobsStore.getAll(),
      applicationsStore.getAll(),
      settingsStore.get("profile"),
    ]);
    const byUrl = new Map<string, JobRecord>();
    const usedIds = new Set<string>();
    for (const job of jobs) {
      const url = normalizeSyncUrl(job.detailUrl);
      if (url) {
        byUrl.set(url, job);
      }
      usedIds.add(job.id);
    }

    const incomingJobs = new Map<string, SyncJob>();
    for (const item of snapshot.jobs ?? []) {
      const url = normalizeSyncUrl(item.detail_url);
      if (!url) {
        continue;
      }
      const previous = incomingJobs.get(url);
      if (!previous || isNewer(item.last_seen_at, previous.last_seen_at)) {
        incomingJobs.set(url, item);
      }
    }

    for (const [url, incoming] of incomingJobs) {
      const existing = byUrl.get(url);
      if (!existing) {
        const id = incoming.id && !usedIds.has(incoming.id) ? incoming.id : newId();
        usedIds.add(id);
        const created: JobRecord = {
          id,
          title: incoming.title,
          company: incoming.company,
          city: incoming.city ?? null,
          source: incoming.source || incoming.company,
          detailUrl: url,
          jdRaw: incoming.jd_raw ?? null,
          captureStatus: incoming.capture_status,
          matchScore: incoming.match_score ?? null,
          lastSeenAt: incoming.last_seen_at ?? new Date().toISOString(),
          scoreSummary: incoming.score_summary ?? null,
          scoreAdvantages: incoming.score_advantages ?? null,
          scoreGaps: incoming.score_gaps ?? null,
          scoreModel: incoming.score_model ?? null,
          scorePromptVersion: incoming.score_prompt_version ?? null,
          scoredAt: incoming.scored_at ?? null,
        };
        byUrl.set(url, created);
        counts.jobs_created += 1;
        writes.push(jobsStore.put(created));
        continue;
      }

      const incomingNewer = isNewer(incoming.last_seen_at, existing.lastSeenAt);
      let merged: JobRecord = { ...existing };
      if (incomingNewer) {
        merged = {
          ...merged,
          title: incoming.title,
          company: incoming.company,
          city: incoming.city ?? merged.city,
          source: incoming.source || merged.source,
          detailUrl: url,
          jdRaw: incoming.jd_raw ?? merged.jdRaw,
          captureStatus:
            incoming.capture_status === "unknown"
              ? merged.captureStatus
              : incoming.capture_status,
          lastSeenAt: incoming.last_seen_at ?? merged.lastSeenAt,
        };
      } else {
        // 较旧的快照只能补空，不能覆盖较新一方的字段。
        merged = {
          ...merged,
          city: merged.city ?? incoming.city ?? null,
          source: merged.source || incoming.source || incoming.company,
          jdRaw: merged.jdRaw ?? incoming.jd_raw ?? null,
          captureStatus:
            merged.captureStatus === "unknown" && incoming.capture_status !== "unknown"
              ? incoming.capture_status
              : merged.captureStatus,
        };
      }
      if (scoreIsNewer(existing, incoming)) {
        merged = copyScore(merged, incoming);
      }

      if (sameJob(existing, merged)) {
        counts.jobs_kept += 1;
      } else {
        counts.jobs_updated += 1;
        writes.push(jobsStore.put(merged));
      }
      byUrl.set(url, merged);
    }

    const byJob = new Map(applications.map((app) => [app.jobId, app]));
    const incomingApplications = new Map<string, SyncApplication>();
    for (const item of snapshot.applications ?? []) {
      const url = normalizeSyncUrl(item.job_detail_url ?? "");
      if (!url) {
        continue;
      }
      const previous = incomingApplications.get(url);
      if (!previous || isNewer(item.updated_at, previous.updated_at)) {
        incomingApplications.set(url, item);
      }
    }

    for (const [url, incoming] of incomingApplications) {
      const job = byUrl.get(url);
      if (!job) {
        counts.applications_skipped += 1;
        continue;
      }
      const existing = byJob.get(job.id);
      if (existing) {
        if (isNewer(incoming.updated_at, existing.updatedAt)) {
          const updated: ApplicationRecord = {
            ...existing,
            title: incoming.title,
            company: incoming.company,
            stage: incoming.stage as ApplicationStage,
            note: incoming.note ?? null,
            appliedAt: incoming.applied_at,
            updatedAt: incoming.updated_at,
          };
          byJob.set(job.id, updated);
          counts.applications_updated += 1;
          writes.push(applicationsStore.put(updated));
        } else {
          counts.applications_kept += 1;
        }
        continue;
      }
      const created: ApplicationRecord = {
        id: newId(),
        jobId: job.id,
        title: incoming.title,
        company: incoming.company,
        stage: incoming.stage as ApplicationStage,
        note: incoming.note ?? null,
        appliedAt: incoming.applied_at,
        updatedAt: incoming.updated_at,
      };
      byJob.set(job.id, created);
      counts.applications_created += 1;
      writes.push(applicationsStore.put(created));
    }

    if (snapshot.profile) {
      const local = storedProfile as ProfileRecord | undefined;
      if (!local || isNewer(snapshot.profile.updated_at, local.updatedAt)) {
        const profile: ProfileRecord = {
          key: "profile",
          keywords: snapshot.profile.keywords ?? [],
          directions: snapshot.profile.directions ?? [],
          summary: snapshot.profile.summary,
          updatedAt: snapshot.profile.updated_at,
        };
        writes.push(settingsStore.put(profile));
        counts.profile_updated = true;
      }
    }

    await Promise.all(writes);
    await tx.done;
    return counts;
  } catch (error) {
    // 同步抛错（例如不可结构化克隆的数据）不会自动回滚已排队的请求，
    // 必须显式中止，兑现「整份成功或整份失败」。
    try {
      tx.abort();
    } catch {
      // 事务已经结束（提交完成或请求错误已触发 abort）。
    }
    await Promise.allSettled(writes);
    await tx.done.catch(() => undefined);
    throw error;
  }
}

function summarizeOne(label: string, counts: SyncCounts): string {
  const profile = counts.profile_updated ? "，画像已更新" : "";
  const skipped = counts.applications_skipped
    ? `，跳过 ${counts.applications_skipped} 条无岗位投递`
    : "";
  return `${label}：岗位 +${counts.jobs_created}/~${counts.jobs_updated}，投递 +${counts.applications_created}/~${counts.applications_updated}${profile}${skipped}`;
}

function summarize(mode: SyncMode, local: SyncCounts, desktop: SyncCounts | null): string {
  if (mode === "push") {
    return `已推送。${summarizeOne("电脑", desktop ?? emptyCounts())}`;
  }
  if (mode === "pull") {
    return `已拉取。${summarizeOne("手机", local)}`;
  }
  return `双向完成。${summarizeOne("电脑", desktop ?? emptyCounts())}；${summarizeOne("手机", local)}`;
}

function readableSyncError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes("HTTP 401")) {
    return "访问码不正确：请确认电脑端终端里显示的访问码。";
  }
  if (message.includes("HTTP 403")) {
    return "电脑端拒绝了请求：请确认手机与电脑在同一 Wi-Fi，且地址正确。";
  }
  if (message.includes("HTTP 404")) {
    return "电脑端版本过旧：请升级到带同步功能的新版本。";
  }
  if (message.includes("TimeoutError") || message.includes("超时")) {
    return "连接电脑超时：请确认电脑端的 Hunter1 仍在运行。";
  }
  return message;
}

export async function syncWithDesktop(mode: SyncMode): Promise<SyncOutcome> {
  let base: string;
  let accessCode: string;
  try {
    const config = await loadSyncConfig();
    base = normalizeDesktopUrl(config.desktopUrl);
    accessCode = config.accessCode.trim();
    if (!accessCode) {
      throw new Error("请填写电脑端终端里显示的访问码");
    }
  } catch (error) {
    throw new Error(readableSyncError(error), { cause: error });
  }

  const headers = { "X-Hunter1-Access-Code": accessCode };
  let local = emptyCounts();
  let desktop: SyncCounts | null = null;
  try {
    if (mode === "pull") {
      const snapshot = await requestJson<SyncSnapshot>(`${base}/api/sync/snapshot`, {
        headers,
        timeoutMs: 60_000,
      });
      local = await mergeLocalSnapshot(snapshot);
    } else {
      const snapshot = await buildLocalSnapshot();
      const response = await requestJson<SyncMergeResponse>(`${base}/api/sync/merge`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: snapshot,
        timeoutMs: 60_000,
      });
      desktop = response.counts;
      if (mode === "both") {
        local = await mergeLocalSnapshot(response.snapshot);
      }
    }
  } catch (error) {
    throw new Error(readableSyncError(error), { cause: error });
  }

  const message = summarize(mode, local, desktop);
  await saveSyncConfig({ lastSyncAt: new Date().toISOString(), lastResult: message });
  return { local, desktop, message };
}
