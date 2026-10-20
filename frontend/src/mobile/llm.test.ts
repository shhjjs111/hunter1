import "fake-indexeddb/auto";

import { beforeEach, describe, expect, it } from "vitest";

import { resetDBForTests, saveLLM, saveProfile, type JobRecord } from "./db";
import {
  assistantReply,
  getSettings,
  maskKey,
  saveSettings,
  scoreJob,
  settingsView,
  testConnection,
} from "./llm";

const realFetch = globalThis.fetch;

beforeEach(async () => {
  await resetDBForTests();
  globalThis.fetch = realFetch;
});

function job(): JobRecord {
  return {
    id: "job-1",
    title: "后端实习生",
    company: "示例公司",
    city: "上海",
    source: "实习僧",
    detailUrl: "https://example.invalid/1",
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
  };
}

function completion(content: string): Response {
  return new Response(
    JSON.stringify({ model: "demo", choices: [{ message: { content } }] }),
    { status: 200 },
  );
}

describe("模型配置视图", () => {
  it("空配置返回 null，长 key 只给掩码", async () => {
    expect(settingsView(null)).toBeNull();
    expect(maskKey("")).toBe("");
    expect(maskKey("short")).toBe("•••••");
    expect(maskKey("1234567890abcdef")).toContain("•");
    expect(await getSettings()).toBeNull();
  });

  it("明文 HTTP 指向公网时给出 warning，回环不警告", () => {
    expect(
      settingsView({
        key: "llm",
        baseUrl: "http://example.com/v1",
        model: "m",
        apiKey: "k",
      })?.warning,
    ).toContain("明文 HTTP");
    expect(
      settingsView({
        key: "llm",
        baseUrl: "http://127.0.0.1:8000/v1",
        model: "m",
        apiKey: "k",
      })?.warning,
    ).toBeNull();
  });

  it("保存时空 key 报可读错误", async () => {
    await expect(
      saveSettings({ base_url: "https://api.example.com/v1", model: "m", api_key: "" }),
    ).rejects.toThrow("API Key 不能为空");
  });
});

describe("模型探测与评分", () => {
  it("未配置时探测与评分都给出行动指引", async () => {
    expect(await testConnection({ base_url: "", model: "", api_key: "" })).toMatchObject({
      ok: false,
    });
    await expect(scoreJob(job())).rejects.toThrow("模型未配置");
    await expect(assistantReply("hi", [])).rejects.toThrow("模型未配置");
  });

  it("探测遇到 401 时返回可读原因", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "m", apiKey: "bad" });
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: { message: "bad key" } }), {
        status: 401,
      })) as typeof fetch;
    const result = await testConnection({
      base_url: "https://api.example.com/v1",
      model: "m",
      api_key: "bad",
    });
    expect(result.ok).toBe(false);
    expect(result.message).toContain("401");
  });

  it("评分解析围栏 JSON 并拒绝超范围分数", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "m", apiKey: "k" });
    await saveProfile({ keywords: ["后端"], directions: [], summary: "" });

    globalThis.fetch = (async () =>
      completion('```json\n{"score":88,"advantages":"A","gaps":"G","summary":"S"}\n```')) as typeof fetch;
    await expect(scoreJob(job())).resolves.toMatchObject({ score: 88, summary: "S" });

    globalThis.fetch = (async () => completion('{"score":101}')) as typeof fetch;
    await expect(scoreJob(job())).rejects.toThrow("0-100");

    globalThis.fetch = (async () => completion("not json")) as typeof fetch;
    await expect(scoreJob(job())).rejects.toThrow("非 JSON");
  });

  it("助手回复走同一条 chat 通道", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "m", apiKey: "k" });
    globalThis.fetch = (async () => completion("先看岗位库。")) as typeof fetch;
    await expect(assistantReply("怎么开始", [])).resolves.toBe("先看岗位库。");
  });
});