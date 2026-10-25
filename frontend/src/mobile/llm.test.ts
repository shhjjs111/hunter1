import "fake-indexeddb/auto";

import { beforeEach, describe, expect, it } from "vitest";

import { getLLM, resetDBForTests, saveLLM, saveProfile, type JobRecord } from "./db";
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

  it("保存与探测都拒绝非法端点", async () => {
    await expect(
      saveSettings({ base_url: "not-a-url", model: "m", api_key: "k" }),
    ).rejects.toThrow("合法的 http");

    const probe = await testConnection({ base_url: "not-a-url", model: "m", api_key: "k" });
    expect(probe).toMatchObject({ ok: false });
    expect(probe.message).toContain("合法的 http");
  });

  it("端点变更后不会把旧 Key 复用并发送到新地址", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "m", apiKey: "secret" });

    await expect(
      saveSettings({ base_url: "https://evil.example.com/v1", model: "m", api_key: "" }),
    ).rejects.toThrow("端点地址已改变");
    expect((await getLLM())?.apiKey).toBe("secret");

    let called = false;
    globalThis.fetch = (async () => {
      called = true;
      return completion("OK");
    }) as typeof fetch;
    const probe = await testConnection({
      base_url: "https://evil.example.com/v1",
      model: "m",
      api_key: "",
    });
    expect(probe.ok).toBe(false);
    expect(probe.message).toContain("重新输入 API Key");
    expect(called).toBe(false);
  });

  it("同一 origin 内换路径仍可复用旧 Key", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "m", apiKey: "secret" });
    globalThis.fetch = (async () => completion("OK")) as typeof fetch;

    const probe = await testConnection({
      base_url: "https://api.example.com/v2",
      model: "m",
      api_key: "",
    });
    expect(probe.ok).toBe(true);
  });
});

describe("模型探测与评分", () => {
  it("评分结论与模型名限长，结果可通过同步导入约束", async () => {
    await saveLLM({ baseUrl: "https://api.example.com/v1", model: "demo", apiKey: "key" });
    await saveProfile({ keywords: ["AI"], directions: [], summary: "" });
    globalThis.fetch = (async () => new Response(JSON.stringify({
      model: "m".repeat(129),
      choices: [{ message: { content: JSON.stringify({
        score: 88, advantages: "优".repeat(2001), gaps: "差".repeat(2001), summary: "结".repeat(2001),
      }) } }],
    }))) as typeof fetch;
    const result = await scoreJob(job());
    expect(result.advantages).toHaveLength(2000);
    expect(result.gaps).toHaveLength(2000);
    expect(result.summary).toHaveLength(2000);
    expect(result.model).toHaveLength(128);
  });
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
    expect(result.message).toContain("鉴权");
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
