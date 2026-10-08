/**
 * SSE 订阅原语 —— 用 fetch 而不是 EventSource。
 *
 * 为什么不用 EventSource：它只能 GET，而对话要带 message 与 conversation_id。
 * fetch + ReadableStream 是唯一能 POST 且边收边解的做法。
 *
 * 解析规则（SSE 协议）：事件以空行分隔，每个事件由若干 `field: value` 行组成。
 * 这里只关心 `data:`（本项目不使用 event/id/retry 字段）。
 */

export type SseHandler = (event: Record<string, unknown>) => void;

export async function streamSse(
  url: string,
  body: unknown,
  onEvent: SseHandler,
  signal?: AbortSignal,
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
      const payload = parseBlock(block);
      if (payload !== null) {
        onEvent(payload);
      }
    }
  }

  // 收尾：流结束时 buffer 里可能还有最后一条（某些实现末尾不带空行），
  // 并且解码器里可能还扣着半个 UTF-8 序列 —— `decode()` 不收尾就**静默丢**。
  buffer += decoder.decode();
  const tail = parseBlock(buffer);
  if (tail !== null) {
    onEvent(tail);
  }
}

/**
 * 从开流前的错误体里取出**给人看**的原因。
 *
 * 后端失败给的是 `{"detail":"模型未配置：…"}`（可读原因）；直接把整段 JSON
 * slice 出去会连花括号一起甩给用户（`请求失败（HTTP 409）：{"detail":"…"}`）。
 * 与 `shared/api/errors.ts` 的 apiErrorMessage 同一取舍：有 detail 就取它。
 */
function readableDetail(text: string): string {
  if (!text) {
    return "";
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed !== null && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim() !== "") {
        return `：${detail}`;
      }
    }
  } catch {
    // 不是 JSON —— 原样截断展示
  }
  return `：${text.slice(0, 300)}`;
}

function parseBlock(block: string): Record<string, unknown> | null {
  const dataLines = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trim());

  if (dataLines.length === 0) {
    return null;
  }
  const raw = dataLines.join("\n");
  if (!raw) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    // 非法 JSON 不该让整条流崩掉：跳过这一条，后面的还能收
    console.warn("收到无法解析的 SSE 事件：", raw.slice(0, 200));
    return null;
  }
}
