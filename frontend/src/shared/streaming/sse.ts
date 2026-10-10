/**
 * SSE 订阅原语 —— 用 fetch 而不是 EventSource。
 *
 * 为什么不用 EventSource：它只能 GET，而对话要带 message 与 conversation_id。
 * fetch + ReadableStream 是唯一能 POST 且边收边解的做法。
 *
 * 解析规则（SSE 协议）：事件以空行分隔，每个事件由若干 `field: value` 行组成。
 * 这里只关心 `data:`（本项目不使用 event/id/retry 字段）。
 */

import { detailText } from "../api/errors";

export type SseHandler = (event: Record<string, unknown>) => void;
/** 有 `data:` 行却没能解析成事件的块（内容丢了，调用方该告诉用户）。 */
export type SseMalformedHandler = (raw: string) => void;

export async function streamSse(
  url: string,
  body: unknown,
  onEvent: SseHandler,
  signal?: AbortSignal,
  onMalformed?: SseMalformedHandler,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    // 开流前的失败（409/422/500）走普通 JSON 错误体；把它读出来给用户
    const text = await response.text().catch(() => "");
    throw new Error(`请求失败（HTTP ${response.status}）${readableDetail(text)}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    // SSE 允许 CRLF（\r\n）作行结束 —— 反向代理与部分框架默认就发 CRLF。
    // 先归一成 \n 再切分：否则 "\r\n\r\n" 不含 "\n\n"，事件根本切不开。
    // 只替换成对的 \r\n（不碰裸 \r）：若跨 chunk 边界把 \r 提前转成 \n，
    // 会与下一 chunk 的 \n 拼出假空行，把一条事件误切成两条。
    buffer = buffer.replace(/\r\n/g, "\n");

    // 事件之间以空行分隔；最后一段可能不完整，留在 buffer 里等下一个 chunk
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      dispatch(parseBlock(block), onEvent, onMalformed);
    }
  }

  // 收尾：流结束时 buffer 里可能还有最后一条（某些实现末尾不带空行），
  // 并且解码器里可能还扣着半个 UTF-8 序列 —— `decode()` 不收尾就**静默丢**。
  buffer += decoder.decode();
  dispatch(parseBlock(buffer), onEvent, onMalformed);
}

/**
 * 从开流前的错误体里取出**给人看**的原因。
 *
 * 后端失败给的是 `{"detail":"模型未配置：…"}`（可读原因）；直接把整段 JSON
 * slice 出去会连花括号一起甩给用户（`请求失败（HTTP 409）：{"detail":"…"}`）。
 * detail 的取法**复用** `shared/api/errors.ts` 的 `detailText`（那里与
 * `apiErrorMessage` 共用同一份规则：字符串、FastAPI 的 `[{loc,msg,type}]` 数组都认），
 * 本文件不再自己写一份只认字符串 detail 的重复实现。
 */
function readableDetail(text: string): string {
  if (!text) {
    return "";
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed !== null && typeof parsed === "object" && "detail" in parsed) {
      const readable = detailText((parsed as { detail?: unknown }).detail);
      if (readable !== null) {
        return `：${readable}`;
      }
    }
  } catch {
    // 不是 JSON —— 原样截断展示
  }
  return `：${text.slice(0, 300)}`;
}

/** 一个 SSE 块的解析结果：`skip` = 本就不是事件（心跳/注释），`dropped` = 有 data 行却丢了内容。 */
type ParsedBlock =
  | { kind: "event"; event: Record<string, unknown> }
  | { kind: "skip" }
  | { kind: "dropped"; raw: string };

/** 把解析结果派发给调用方：事件走 onEvent，丢内容走 onMalformed（本就不是事件则什么都不做）。 */
function dispatch(
  parsed: ParsedBlock,
  onEvent: SseHandler,
  onMalformed?: SseMalformedHandler,
): void {
  if (parsed.kind === "event") {
    onEvent(parsed.event);
  } else if (parsed.kind === "dropped") {
    onMalformed?.(parsed.raw);
  }
}

function parseBlock(block: string): ParsedBlock {
  const dataLines = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trim());

  if (dataLines.length === 0) {
    // 没有 data 行 —— 心跳/注释块，不是事件，谈不上「丢内容」
    return { kind: "skip" };
  }
  const raw = dataLines.join("\n");
  if (!raw) {
    // 空的 `data:`（冒号后什么都没有）没有内容可丢 —— 协议允许的「空数据」，
    // 不是解析失败。原先归进 dropped，调用方据此报「有内容未能解析」纯属误报。
    return { kind: "skip" };
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null
      ? { kind: "event", event: parsed as Record<string, unknown> }
      : { kind: "dropped", raw };
  } catch {
    // 非法 JSON 不该让整条流崩掉：跳过这一条，后面的还能收。
    // 但**不能只写控制台** —— 静默丢一条 text 事件 = 回答缺段，
    // 界面却把它当完整结果呈现（与 AssistantPage 对「静默截断」的警惕矛盾）。
    console.warn("收到无法解析的 SSE 事件：", raw.slice(0, 200));
    return { kind: "dropped", raw };
  }
}
