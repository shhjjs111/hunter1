import { describe, expect, it, vi } from "vitest";

import { streamSse } from "./sse";

/** 造一个可分段推送的 ReadableStream，模拟真实网络的断续到达。 */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index++]));
    },
  });
}

function stubFetch(chunks: string[], status = 200): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async () => new Response(streamOf(chunks), { status }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("streamSse", () => {
  it("解析单条事件", async () => {
    stubFetch(['data: {"type":"text","text":"你好"}\n\n']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "text", text: "你好" }]);
  });

  it("跨 chunk 切断的事件能拼回来", async () => {
    // 真实网络里一个事件可能被切成几个 TCP 段 —— 这是最容易写错的地方
    stubFetch(['data: {"type":"te', 'xt","text":"拼', '接"}\n\n']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "text", text: "拼接" }]);
  });

  it("兼容 CRLF 行结束（代理/框架默认可能发 \\r\\n）", async () => {
    // \r\n\r\n 不含 \n\n —— 不归一化的话整条流会被当成一个切不开的块
    stubFetch(['data: {"type":"text","text":"a"}\r\n\r\ndata: {"type":"done"}\r\n\r\n']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "text", text: "a" }, { type: "done" }]);
  });

  it("CRLF 跨 chunk 切断也能拼回，且不误切", async () => {
    // \r 与 \n 分属两个 chunk：提前把裸 \r 转成 \n 会与下一段拼出假空行
    stubFetch(['data: {"type":"a"}\r\n\r\n', 'data: {"type":"b"}\r\n\r\n']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "a" }, { type: "b" }]);
  });

  it("多条事件按序到达", async () => {
    stubFetch([
      'data: {"type":"text","text":"a"}\n\n',
      'data: {"type":"text","text":"b"}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    const types: unknown[] = [];
    await streamSse("/x", {}, (event) => types.push(event.type));
    expect(types).toEqual(["text", "text", "done"]);
  });

  it("非法 JSON 跳过但不中断流", async () => {
    stubFetch(['data: {坏掉的\n\n', 'data: {"type":"done"}\n\n']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "done" }]);
  });

  it("末尾无空行的事件也能收到", async () => {
    stubFetch(['data: {"type":"done"}']);
    const events: Record<string, unknown>[] = [];
    await streamSse("/x", {}, (event) => events.push(event));
    expect(events).toEqual([{ type: "done" }]);
  });

  it("非 2xx 抛出可读错误（开流前的失败走 JSON 体）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"模型未配置"}', { status: 500 })),
    );
    await expect(streamSse("/x", {}, () => {})).rejects.toThrow(/HTTP 500.*模型未配置/);
  });

  it("错误体里的 detail 被取出，不把原始 JSON 甩给用户", async () => {
    // 直接 slice 原始 JSON 会得到 `请求失败（HTTP 409）：{"detail":"…"}`——
    // 花括号一起给用户。这里断言消息里**不含**那段 JSON 包装。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"画像未配置：请先去配置页"}', { status: 409 })),
    );
    await expect(streamSse("/x", {}, () => {})).rejects.toThrow(
      "请求失败（HTTP 409）：画像未配置：请先去配置页",
    );
  });
});
