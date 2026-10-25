import "fake-indexeddb/auto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDB,
  getProfile,
  resetDBForTests,
  saveProfile,
  type ApplicationRecord,
  type JobRecord,
} from "./db";
import {
  buildLocalSnapshot,
  loadSyncConfig,
  mergeLocalSnapshot,
  normalizeDesktopUrl,
  normalizeSyncUrl,
  saveSyncConfig,
  syncWithDesktop,
  type SyncApplication,
  type SyncCounts,
  type SyncJob,
  type SyncSnapshot,
} from "./sync";

const NOW = "2026-10-20T12:00:00.000Z";
const LATER = "2026-10-20T13:00:00.000Z";
const URL = "https://example.com/jobs/1";

function counts(overrides: Partial<SyncCounts> = {}): SyncCounts {
  return {
    jobs_created: 0,
    jobs_updated: 0,
    jobs_kept: 0,
    applications_created: 0,
    applications_updated: 0,
    applications_kept: 0,
    applications_skipped: 0,
    profile_updated: false,
    ...overrides,
  };
}

function jobRecord(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: "local-job",
    title: "AI产品经理",
    company: "字节跳动",
    city: "北京",
    source: "实习僧",
    detailUrl: URL,
    jdRaw: "旧 JD",
    captureStatus: "complete",
    matchScore: null,
    lastSeenAt: NOW,
    scoreSummary: null,
    scoreAdvantages: null,
    scoreGaps: null,
    scoreModel: null,
    scorePromptVersion: null,
    scoredAt: null,
    ...overrides,
  };
}

function applicationRecord(overrides: Partial<ApplicationRecord> = {}): ApplicationRecord {
  return {
    id: "local-app",
    jobId: "local-job",
    title: "AI产品经理",
    company: "字节跳动",
    stage: "applied",
    note: null,
    appliedAt: "2026-10-18T12:00:00.000Z",
    updatedAt: NOW,
    ...overrides,
  };
}

function syncJob(overrides: Partial<SyncJob> = {}): SyncJob {
  return {
    id: "remote-job",
    title: "AI产品经理",
    company: "字节跳动",
    source: "实习僧",
    detail_url: URL,
    capture_status: "complete",
    last_seen_at: LATER,
    ...overrides,
  };
}

function syncApplication(overrides: Partial<SyncApplication> = {}): SyncApplication {
  return {
    job_detail_url: URL,
    title: "AI产品经理",
    company: "字节跳动",
    stage: "interview",
    applied_at: "2026-10-18T12:00:00.000Z",
    updated_at: LATER,
    ...overrides,
  };
}

function snapshot(overrides: Partial<SyncSnapshot> = {}): SyncSnapshot {
  return {
    version: 1,
    exported_at: NOW,
    jobs: [],
    applications: [],
    profile: null,
    ...overrides,
  };
}

beforeEach(async () => {
  await resetDBForTests();
});

it.each([false, true])("同 URL 副本分别保留最新事实与最新评分（倒序=%s）", async (reverse) => {
  const items = [
    syncJob({ title: "新事实", last_seen_at: LATER, match_score: 20, scored_at: NOW }),
    syncJob({ title: "旧事实", last_seen_at: NOW, match_score: 91, scored_at: LATER, score_summary: "新结论" }),
  ];
  if (reverse) items.reverse();
  const result = await mergeLocalSnapshot(snapshot({ jobs: items }));
  const db = await getDB();
  expect(result.jobs_created).toBe(1);
  expect((await db.getAll("jobs"))[0]).toMatchObject({ title: "新事实", matchScore: 91, scoreSummary: "新结论" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("URL 归一化", () => {
  it("去掉跟踪参数、末尾斜杠与普通锚点，保留 hash 路由", () => {
    expect(normalizeSyncUrl(`${URL}/?utm_source=x&jobId=1#section`)).toBe(`${URL}?jobId=1`);
    expect(normalizeSyncUrl("https://example.com/jobs#/job/1")).toBe(
      "https://example.com/jobs#/job/1",
    );
    expect(normalizeSyncUrl("javascript:void(0)")).toBe("");
  });

  it("电脑地址允许省略 scheme，并只保留 origin", () => {
    expect(normalizeDesktopUrl("192.168.1.5:8000/api")).toBe("http://192.168.1.5:8000");
    expect(normalizeDesktopUrl("https://hunter.example.com/")).toBe("https://hunter.example.com");
    expect(() => normalizeDesktopUrl("")).toThrow(/请填写电脑地址/);
    expect(() => normalizeDesktopUrl("http://example.com:8000")).toThrow(/公网 HTTP/);
  });
});

describe("本地快照", () => {
  it("导出岗位、投递与画像，并按岗位 URL 对齐投递", async () => {
    const db = await getDB();
    await db.put("jobs", jobRecord());
    await db.put("applications", applicationRecord());
    await saveProfile({ keywords: ["AI"], directions: [], summary: "背景", updatedAt: NOW });

    const local = await buildLocalSnapshot();

    expect(local.jobs?.[0].detail_url).toBe(URL);
    expect(local.applications?.[0].job_detail_url).toBe(URL);
    expect(local.profile?.keywords).toEqual(["AI"]);
    expect(local.profile?.updated_at).toBe(NOW);
  });
});

describe("合并快照", () => {
  it("新岗位按 URL 创建，旧快照不会回滚较新的标题", async () => {
    const first = await mergeLocalSnapshot(snapshot({ jobs: [syncJob()] }));
    expect(first.jobs_created).toBe(1);

    const db = await getDB();
    expect((await db.getAll("jobs"))[0].title).toBe("AI产品经理");

    const second = await mergeLocalSnapshot(
      snapshot({ jobs: [syncJob({ title: "旧标题", last_seen_at: NOW })] }),
    );
    expect(second.jobs_kept).toBe(1);
    expect((await db.getAll("jobs"))[0].title).toBe("AI产品经理");
  });

  it("旧快照只补空：不覆盖较新的 JD 与抓取状态", async () => {
    const db = await getDB();
    await db.put("jobs", jobRecord({ jdRaw: "新 JD", lastSeenAt: LATER }));

    await mergeLocalSnapshot(
      snapshot({
        jobs: [
          syncJob({
            title: "旧标题",
            jd_raw: "旧 JD",
            capture_status: "failed",
            last_seen_at: NOW,
          }),
        ],
      }),
    );

    const stored = (await db.getAll("jobs"))[0];
    expect(stored.title).toBe("AI产品经理");
    expect(stored.jdRaw).toBe("新 JD");
    expect(stored.captureStatus).toBe("complete");
  });

  it("投递按 job_detail_url 对齐，较新的阶段胜出", async () => {
    const db = await getDB();
    await db.put("jobs", jobRecord());
    await db.put("applications", applicationRecord({ stage: "applied", updatedAt: NOW }));

    const result = await mergeLocalSnapshot(snapshot({ applications: [syncApplication()] }));

    expect(result.applications_updated).toBe(1);
    const applications = await db.getAll("applications");
    expect(applications[0].jobId).toBe("local-job");
    expect(applications[0].stage).toBe("interview");
  });

  it("找不到岗位的投递被跳过，不会造半条记录", async () => {
    const result = await mergeLocalSnapshot(
      snapshot({
        applications: [
          syncApplication({ job_detail_url: "https://unknown.example/job/9" }),
        ],
      }),
    );
    expect(result.applications_skipped).toBe(1);
  });

  it("画像按更新时间取新", async () => {
    await saveProfile({ keywords: ["旧"], directions: [], summary: "", updatedAt: NOW });

    const result = await mergeLocalSnapshot(
      snapshot({
        profile: { keywords: ["新"], directions: [], summary: "", updated_at: LATER },
      }),
    );

    expect(result.profile_updated).toBe(true);
    expect((await getProfile())?.keywords).toEqual(["新"]);
  });

  it("中途写入失败时整份回滚", async () => {
    const badProfile = {
      keywords: [() => "not-cloneable"],
      directions: [],
      summary: "",
      updated_at: LATER,
    } as unknown as SyncSnapshot["profile"];

    await expect(
      mergeLocalSnapshot(snapshot({ jobs: [syncJob()], profile: badProfile })),
    ).rejects.toBeDefined();

    const db = await getDB();
    expect(await db.getAll("jobs")).toEqual([]);
    expect(await db.getAll("applications")).toEqual([]);
    expect(await getProfile()).toBeNull();
  });
});

describe("与电脑同步", () => {
  it("双向同步发送访问码头，并把电脑返回的快照合并回手机", async () => {
    await saveSyncConfig({ desktopUrl: "192.168.1.5:8000", accessCode: "code-123" });
    const remote = snapshot({ jobs: [syncJob()] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      expect(request.headers.get("x-hunter1-access-code")).toBe("code-123");
      expect(request.method).toBe("POST");
      const body = (await request.json()) as SyncSnapshot;
      expect(body.version).toBe(1);
      return new Response(
        JSON.stringify({ counts: counts({ jobs_created: 1 }), snapshot: remote }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const outcome = await syncWithDesktop("both");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(outcome.desktop?.jobs_created).toBe(1);
    expect(outcome.local.jobs_created).toBe(1);
    expect(outcome.message).toContain("双向完成");
    expect((await loadSyncConfig()).lastSyncAt).not.toBeNull();
  });

  it("只拉取时走 GET，并把岗位写进本地", async () => {
    await saveSyncConfig({ desktopUrl: "http://192.168.1.5:8000", accessCode: "code-123" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        expect(request.method).toBe("GET");
        expect(request.url).toBe("http://192.168.1.5:8000/api/sync/snapshot");
        return new Response(JSON.stringify(snapshot({ jobs: [syncJob()] })), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const outcome = await syncWithDesktop("pull");

    expect(outcome.local.jobs_created).toBe(1);
    await expect((await getDB()).getAll("jobs")).resolves.toHaveLength(1);
  });

  it("401 给出可行动的访问码提示", async () => {
    await saveSyncConfig({ desktopUrl: "192.168.1.5:8000", accessCode: "wrong" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "需要访问码" }), { status: 401 })),
    );

    await expect(syncWithDesktop("both")).rejects.toThrow(/访问码不正确/);
  });
});
