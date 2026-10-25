import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export type ApplicationStage =
  | "applied"
  | "written_test"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn";

export type CaptureStatus = "unknown" | "pending" | "complete" | "failed";

export interface LLMRecord {
  key: "llm";
  baseUrl: string;
  model: string;
  apiKey: string;
}

export interface ProfileRecord {
  key: "profile";
  keywords: string[];
  directions: string[];
  summary: string;
  /** 最后一次保存时间；旧记录可能没有，同步时按最旧处理。 */
  updatedAt?: string;
}

export interface MetaRecord {
  key: "meta";
  lastCrawlAt: string | null;
  /** 最近一次「尝试」开始时间；失败时用来做冷却，避免每次前台都重试。 */
  lastCrawlAttemptAt?: string | null;
  autoCrawl: boolean;
}

export interface SyncRecord {
  key: "sync";
  desktopUrl: string;
  accessCode: string;
  lastSyncAt: string | null;
  lastResult: string | null;
}

export interface JobRecord {
  id: string;
  title: string;
  company: string;
  city: string | null;
  source: string;
  detailUrl: string;
  jdRaw: string | null;
  captureStatus: CaptureStatus;
  matchScore: number | null;
  lastSeenAt: string;
  scoreSummary: string | null;
  scoreAdvantages: string | null;
  scoreGaps: string | null;
  scoreModel: string | null;
  scorePromptVersion: string | null;
  scoredAt: string | null;
}

export interface ApplicationRecord {
  id: string;
  jobId: string;
  title: string;
  company: string;
  stage: ApplicationStage;
  note: string | null;
  appliedAt: string;
  updatedAt: string;
}

export interface ConversationRecord {
  id: string;
  title: string;
  updatedAt: string;
}

export interface MessageRecord {
  id: string;
  conversationId: string;
  role: string;
  content: string;
  createdAt: string;
  /** 同一毫秒内也保持提问与回答的顺序；旧记录按创建时间读取。 */
  sequence?: number;
}

interface HunterDB extends DBSchema {
  settings: { key: string; value: LLMRecord | ProfileRecord | MetaRecord | SyncRecord };
  jobs: {
    key: string;
    value: JobRecord;
    indexes: { "by-detail-url": string; "by-last-seen": string };
  };
  applications: {
    key: string;
    value: ApplicationRecord;
    indexes: { "by-job": string };
  };
  conversations: {
    key: string;
    value: ConversationRecord;
    indexes: { "by-updated": string };
  };
  messages: {
    key: string;
    value: MessageRecord;
    indexes: { "by-conversation": string };
  };
}

const DB_NAME = "hunter1-mobile";
const DB_VERSION = 1;

let database: Promise<IDBPDatabase<HunterDB>> | null = null;

export function getDB(): Promise<IDBPDatabase<HunterDB>> {
  database ??= openDB<HunterDB>(DB_NAME, DB_VERSION, {
    upgrade(db) {
      db.createObjectStore("settings", { keyPath: "key" });
      const jobs = db.createObjectStore("jobs", { keyPath: "id" });
      jobs.createIndex("by-detail-url", "detailUrl", { unique: true });
      jobs.createIndex("by-last-seen", "lastSeenAt");
      const applications = db.createObjectStore("applications", { keyPath: "id" });
      applications.createIndex("by-job", "jobId");
      const conversations = db.createObjectStore("conversations", { keyPath: "id" });
      conversations.createIndex("by-updated", "updatedAt");
      const messages = db.createObjectStore("messages", { keyPath: "id" });
      messages.createIndex("by-conversation", "conversationId");
    },
  });
  return database;
}

export async function resetDBForTests(): Promise<void> {
  if (database) {
    const db = await database;
    db.close();
    database = null;
  }
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase(DB_NAME);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => resolve();
  });
}

export async function getLLM(): Promise<LLMRecord | null> {
  const db = await getDB();
  return ((await db.get("settings", "llm")) as LLMRecord | undefined) ?? null;
}

export async function saveLLM(record: Omit<LLMRecord, "key">): Promise<LLMRecord> {
  const db = await getDB();
  const value: LLMRecord = { key: "llm", ...record };
  await db.put("settings", value);
  return value;
}

export async function getProfile(): Promise<ProfileRecord | null> {
  const db = await getDB();
  return ((await db.get("settings", "profile")) as ProfileRecord | undefined) ?? null;
}

export async function saveProfile(record: Omit<ProfileRecord, "key">): Promise<ProfileRecord> {
  const db = await getDB();
  const value: ProfileRecord = {
    key: "profile",
    ...record,
    updatedAt: record.updatedAt ?? new Date().toISOString(),
  };
  await db.put("settings", value);
  return value;
}

export async function getMeta(): Promise<MetaRecord | null> {
  const db = await getDB();
  return ((await db.get("settings", "meta")) as MetaRecord | undefined) ?? null;
}

export async function saveMeta(record: Omit<MetaRecord, "key">): Promise<MetaRecord> {
  const db = await getDB();
  const value: MetaRecord = { key: "meta", ...record };
  await db.put("settings", value);
  return value;
}

export function newId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
