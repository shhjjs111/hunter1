import { useEffect, useRef, useState } from "react";

import { Button, Card, EmptyState, ErrorNotice, PageHeader } from "../../shared/ui";
import { apiUrl } from "../../shared/api/client";
import { streamSse } from "../../shared/streaming/sse";
import { useConversationMessages, useConversations, useRefreshConversations } from "./api";
import { MessageBubble, type ChatItem } from "./components/MessageBubble";

export function AssistantPage() {
  const conversations = useConversations();
  const [currentId, setCurrentId] = useState<string | null>(null);
  const messages = useConversationMessages(currentId);
  const refresh = useRefreshConversations();

  const [input, setInput] = useState("");
  const [live, setLive] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 本轮回答的「截断/降级」提示（正常回答时为 null）
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 发问那一刻的历史长度：历史随后长出这一截，就说明本轮已经落库，live 可以退场。
  const [turnBaseline, setTurnBaseline] = useState<number | null>(null);
  // live 的镜像，给异步回调读（`send` 闭包里的 live 是提交那一刻的旧值）。
  const liveRef = useRef<ChatItem[]>([]);

  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  // 卸载时中止在飞的流：否则回调会继续对已卸载的组件 setState
  // （切走路由后流还在跑，白耗流量也白改状态）。
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // 历史消息（已落库的）与本次流式（还在飞的）拼在一起展示
  const history: ChatItem[] = (messages.data ?? []).map((item) => ({
    role: item.role,
    content: item.content,
  }));
  // 本轮 live 何时退场：历史已经长出「发问那一刻」的那一截 → 本轮已写进历史。
  //
  // 历史请求失败/慢时这个条件一直为 false，live 因此留在屏幕上 —— 而不是像
  // 「done 里立刻清 live」那样让刚显示的回答凭空消失（后端其实已经落库）。
  // 判据是纯计算，不引入额外渲染帧，所以也不会闪出两份。
  const turnPersisted = turnBaseline !== null && history.length > turnBaseline;
  const items = currentId === null ? live : [...history, ...(turnPersisted ? [] : live)];
  // M8：历史查询的加载/错误态必须显式呈现 —— 否则点开已有会话的瞬间会显示
  // 「新会话」引导语（看起来消息丢了），历史请求失败时永久停在空态、无任何提示。
  const historyLoading = currentId !== null && messages.isLoading;
  const historyError = currentId !== null && messages.isError;

  async function send() {
    const text = input.trim();
    if (!text || streaming) {
      return;
    }
    setInput("");
    setError(null);
    setNotice(null);
    setLive([{ role: "user", content: text }]);
    // 记下「发问那一刻」的历史长度：历史以后长过它 = 本轮落库了
    setTurnBaseline(history.length);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    // 「收到 done」= 整轮正常结束（含截断/降级，那两种也走 done）
    let finished = false;

    try {
      await streamSse(
        apiUrl("/api/assistant/stream"),
        { message: text, conversation_id: currentId ?? "" },
        (event) => {
          if (event.type === "text") {
            appendAssistantText(String(event.text ?? ""));
          } else if (event.type === "tool_start") {
            setLive((prev) => [
              ...prev,
              { role: "tool", content: `正在调用 ${String(event.name)}…` },
            ]);
          } else if (event.type === "tool_end") {
            const ok = event.ok === true;
            setLive((prev) => [
              ...prev,
              {
                role: "tool",
                content: ok
                  ? String(event.content ?? "").slice(0, 300)
                  : `工具失败：${String(event.error ?? "")}`,
              },
            ]);
          } else if (event.type === "done") {
            finished = true;
            const id = String(event.conversation_id ?? "");
            if (id) {
              setCurrentId(id);
            }
            // L7：截断/降级是「回答不完整」的信号，不能当正常结果静默呈现。
            const truncated = event.truncated === true;
            const degraded = event.degraded === true;
            setNotice(
              truncated
                ? "回答达到轮次上限被截断，内容可能不完整。"
                : degraded
                  ? "本次回答由模型降级返回（可能不完整）。"
                  : null,
            );
            // 关键：这里**只 refresh，不清 live**。
            //
            // 后端在 yield `done` **之前**就 `_persist` 落库了（见 router.py 的
            // `_stream_turn`），所以 refresh 拉回来的 history 会包含本轮两条消息。
            // 原实现先 `setLive([])` 再 refresh：历史请求失败/慢时，刚显示出来的
            // 回答会凭空消失（后端其实已经存了）—— 看起来像「消息丢了」。
            // 现在由 `turnPersisted` 判断退场时机：历史确实长出本轮才丢 live。
            refresh();
          } else if (event.type === "error") {
            setError(String(event.message ?? "未知错误"));
          }
        },
        controller.signal,
      );
    } catch (exc) {
      // 用户主动「中止」不是错误 —— 别把 AbortError 当失败弹给用户。
      // 两种形态都要认：`fetch` 在各运行时里的行为不同（有的让 `reader.read()`
      // 抛 AbortError，有的只是把流转成 done 让整条流安静结束）。
      const aborted =
        controller.signal.aborted ||
        (exc instanceof DOMException && exc.name === "AbortError");
      if (!aborted) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }

    // 用户中止了本轮（且没等到 done）：半截回答留在屏幕上是对的（他还要看），
    // 但必须说清它**不会被保存** —— 后端对中止的轮次不落库（见 router 的
    // 「整轮跑完才落库」），切走或刷新后内容就没了，不说清楚就像丢数据。
    if (controller.signal.aborted && !finished) {
      const hasHalfAnswer = liveRef.current.some(
        (item) => item.role === "assistant" && item.content.trim() !== "",
      );
      if (hasHalfAnswer) {
        setNotice("已中止：这段回答没有保存，刷新或切换会话后不会保留。");
      }
    }
  }

  function appendAssistantText(chunk: string) {
    setLive((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === "assistant" && !last.sealed) {
        return [...prev.slice(0, -1), { ...last, content: last.content + chunk }];
      }
      return [...prev, { role: "assistant", content: chunk }];
    });
  }

  return (
    // 窄屏堆叠（会话列表在上、对话区在下），宽屏恢复左侧栏。
    // 224px 的固定侧栏在 390px 窗口里会吃掉近六成宽度。
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      <aside className="md:w-56 md:shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-700">会话</h2>
          <Button
            disabled={streaming}
            onClick={() => {
              setCurrentId(null);
              setLive([]);
              setTurnBaseline(null);
              setError(null);
              setNotice(null);
            }}
          >
            新对话
          </Button>
        </div>
        <ul className="space-y-1">
          {(conversations.data ?? []).map((item) => (
            <li key={item.id}>
              {/* 流式进行中禁止切换：切走会把本轮流内容追加到另一个会话上，
                  且 done 后 currentId 被覆盖回去 —— 用户的选择被静默撤销。 */}
              <button
                type="button"
                disabled={streaming}
                className={`w-full truncate rounded px-2 py-1 text-left text-sm ${
                  currentId === item.id ? "bg-slate-900 text-white" : "hover:bg-slate-100"
                } ${streaming ? "cursor-not-allowed opacity-50" : ""}`}
                onClick={() => {
                  setCurrentId(item.id);
                  setLive([]);
                  setTurnBaseline(null);
                  setError(null);
                  setNotice(null);
                }}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="求职助手" subtitle="只读：它查岗位与投递、给建议；写操作由你确认" />

        <Card className="flex-1 overflow-y-auto p-4" >
          {/* M8 的历史加载/错误提示只**顶替空态**，绝不能盖住消息列表：
              `items` 里含本轮的 live 消息（历史请求失败时更是只有 live）。
              把整块换成提示，会让用户刚发出的问题与正在流式输出的回答凭空消失
              —— 后端其实已经落库，看起来却像「消息丢了」。 */}
          {historyError && (
            <div className="mb-3">
              <ErrorNotice message={`加载会话历史失败：${(messages.error as Error).message}`} />
            </div>
          )}
          {historyLoading && <p className="mb-3 text-sm text-slate-500">加载会话历史…</p>}
          {items.length > 0 && (
            <div className="space-y-3">
              {items.map((item, index) => (
                // key 用「role + 序号」：列表只追加、不重排，序号在流式期间是稳定的
                // —— 同一个气泡的分片一直落在同一位置，DOM 不会被重建。
                //
                // 原先把 `content.slice(0, 32)` 也编进 key：内容一变 key 就变，
                // 于是**前 32 个字符内每个分片都会卸载重建这个气泡**（选区被清、
                // 动画重放）。将来若支持重排/删除，再给消息加真正的 id。
                <MessageBubble key={`${item.role}:${index}`} item={item} />
              ))}
            </div>
          )}
          {items.length === 0 && !historyLoading && !historyError && (
            <EmptyState>问点什么吧，比如「有哪些产品经理的岗位？」</EmptyState>
          )}
        </Card>

        {error !== null && (
          <div className="mt-3">
            <ErrorNotice message={error} />
          </div>
        )}

        {notice !== null && error === null && (
          <p className="mt-3 text-sm text-amber-600">{notice}</p>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2"
            placeholder="有什么想问的？"
            aria-label="输入要问助手的问题"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={streaming}
          />
          <Button type="submit" variant="primary" disabled={streaming || input.trim() === ""}>
            {streaming ? "回复中…" : "发送"}
          </Button>
          {streaming && <Button onClick={() => abortRef.current?.abort()}>中止</Button>}
        </form>
      </div>
    </div>
  );
}
