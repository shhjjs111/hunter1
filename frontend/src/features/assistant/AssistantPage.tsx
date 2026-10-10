import { useEffect, useRef, useState } from "react";

import { Button, Card, EmptyState, ErrorNotice, PageHeader, Pager, fieldClass } from "../../shared/ui";
import { apiUrl } from "../../shared/api/client";
import { streamSse } from "../../shared/streaming/sse";
import { useConversationMessages, useConversations, useRefreshConversations } from "./api";
import { MessageBubble, type ChatItem } from "./components/MessageBubble";

/**
 * 历史里内容等于 `text` 的用户消息条数。
 *
 * 用来判断「本轮是否已落库」：发问时记下这个数，之后比它多一条就说明本轮进了历史。
 * 用**内容**而不是长度：历史里同文本的用户消息数比总长度更能定位「这一轮」，
 * 而且同一句话重复发两轮也认得出来（长度法在这种会话里区分不了）。
 */
function countUserMessages(items: ChatItem[], text: string): number {
  return items.filter((item) => item.role === "user" && item.content.trim() === text).length;
}

export function AssistantPage() {
  const [currentId, setCurrentId] = useState<string | null>(null);
  // 会话列表的页码。列表是**分页**的（后端 `MAX_PAGE_SIZE` = 50）—— 第 51 个起的
  // 会话靠翻页才够得着；没有它那些会话就是「无法触达」，而不是「不可见」。
  const [page, setPage] = useState(1);
  const conversations = useConversations(page);
  const messages = useConversationMessages(currentId);
  const refresh = useRefreshConversations();

  const [input, setInput] = useState("");
  const [live, setLive] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 本轮回答的「截断/降级」提示（正常回答时为 null）
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 本轮发问的**身份**：原文 + 发问那一刻历史里已有几条内容相同的用户消息。
  // 用它判断「这一轮是否已落库」，而不是比历史长度（理由见 turnPersisted 的注释）。
  const [turn, setTurn] = useState<{ text: string; seen: number } | null>(null);
  // 本轮流是否已结束（收到 done，或用户中止）。落库判断必须在它之后。
  const [turnSettled, setTurnSettled] = useState(false);
  // 本轮是否**已经收到过**助手正文（非空白）。中止时用它判断屏幕上有没有半截回答。
  //
  // 为什么是同步置位的 ref，而不是「镜像 live 的 effect」：镜像要等 React 提交之后
  // 才更新，而中止检查就发生在流结束的那段异步流程里 —— 若第一个分片与中止落在同一个
  // 任务内（分片已处理、还没提交），镜像读到的还是空数组，于是「屏幕上有半截回答却
  // 不提示」。实测：让出一个宏任务后再中止，行为是对的；差别只在提交时序。
  // 窗口小于一个宏任务、人手点不到，但**正确性不该依赖调度时序** —— 这里改为在
  // appendAssistantText 里同步置位，与渲染无关。
  const sawAssistantText = useRef(false);
  // 本轮助手正文的分片缓冲：**数组收集**、需要时 join，而不是每片都做一次
  // `last.content + chunk` —— 长回答（几千片）下后者每片都把累积字符串整体复制
  // 一遍，总代价 O(n²)。数组 push 是摊还 O(1)，join 只在真正要渲染时做。
  const assistantChunks = useRef<string[]>([]);
  // 组件是否仍挂载。卸载后到达的流回调 / `send()` 续点一律写不进去 ——
  // 见下面卸载 effect 的注释。
  const alive = useRef(true);

  // 卸载处理：中止在飞的流，并置「已卸载」标记。
  //
  // 只 abort() 不够：abort 停的是网络，而流的回调与 `send()` 的异步续点仍会继续跑
  // —— 它们会继续对**已卸载**的组件 setState（React 18+ 静默忽略，但那是「碰巧
  // 不炸」，正确性不该建在库行为上），`done` 里还会顺带触发一次 assistant 重取。
  // 所以除了 abort，还要用 alive 标记兜住这些写入与副作用。
  useEffect(() => {
    return () => {
      alive.current = false;
      abortRef.current?.abort();
    };
  }, []);

  // 历史消息（已落库的）与本次流式（还在飞的）拼在一起展示
  const history: ChatItem[] = (messages.data ?? []).map((item) => ({
    role: item.role,
    content: item.content,
  }));
  // 本轮 live 何时退场：**流已结束**，且刷新后的历史里确实多了一条本轮的提问。
  //
  // 为什么不能只比历史长度（原实现是 `history.length > turnBaseline`）：
  // `turnBaseline` 取的是发问那一刻的 `history.length`，而「打开已有会话后立刻发问」
  // 时历史请求还在飞 —— 那一刻 history 是 []，baseline=0；历史随后到达（10 条）
  // 就有 10 > 0，被误判成「本轮已落库」，于是**用户刚发的问题和正在流式输出的
  // 回答一起从屏幕上消失**（输入框只以 streaming 为禁用条件，historyLoading 不拦）。
  //
  // 新判据有三道闸：① 流已结束（`turnSettled`）—— 历史中途到达不再能抹掉在飞内容；
  // ② 刷新的那次请求已落地（`messages.isFetching` 为假）—— 避免拿旧数据下判断；
  // ③ 历史里同文本的用户消息**比发问时多了一条** —— 用内容而不是长度，且同一句话
  // 重复发两轮也认得出来。
  const turnPersisted =
    turn !== null &&
    turnSettled &&
    !messages.isFetching &&
    countUserMessages(history, turn.text) > turn.seen;
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
    sawAssistantText.current = false; // 新一轮：清掉上一轮的判断依据
    assistantChunks.current = []; // 新一轮：清空分片缓冲
    // 记下「发问那一刻」历史里已有的同文本条数：以后比它多一条 = 本轮落库了。
    // 历史还没加载时这里是 0 —— 没关系，`turnSettled` 会挡住「历史随后到达」的误判。
    setTurn({ text, seen: countUserMessages(history, text) });
    setTurnSettled(false);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    // 「收到 done」= 整轮正常结束（含截断/降级，那两种也走 done）
    let finished = false;
    // 是否已经明确报过失败（error 事件或异常）—— 用来区分「安静地断掉」与「已报错」，
    // 避免对同一次失败给两条提示。
    let failed = false;
    // 是否有 SSE 块没能解析（内容丢了）。事件被跳过时记下来，流结束再据实告知用户 ——
    // 不记的话：丢一条 text 事件 = 回答缺段，而界面把它当完整结果呈现
    // （后端其实按完整回答落库了，缺的只是这一轮屏幕上的显示）。
    let dropped = false;
    // 本轮 done 事件里是否给出了「回答被截断/降级」的提示。它是**更严重**的信号
    // （回答不完整且已按完整结果落库），不能被「丢块」提示盖掉 —— 见下面的提示优先级。
    let truncatedNotice = false;

    try {
      await streamSse(
        apiUrl("/api/assistant/stream"),
        { message: text, conversation_id: currentId ?? "" },
        (event) => {
          // 卸载后到达的事件一律忽略：不再 setState，也不再触发 done 里的重取。
          if (!alive.current) {
            return;
          }
          if (event.type === "text") {
            appendAssistantText(String(event.text ?? ""));
          } else if (event.type === "tool_start") {
            // 工具气泡之后的助手正文会另起一个气泡 —— 分片缓冲跟着重开。
            assistantChunks.current = [];
            setLive((prev) => [
              ...prev,
              { role: "tool", content: `正在调用 ${String(event.name)}…` },
            ]);
          } else if (event.type === "tool_end") {
            assistantChunks.current = [];
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
            // 流结束了 —— 只有从这里往后，「历史里多了一条本轮的提问」才可信。
            setTurnSettled(true);
            const id = String(event.conversation_id ?? "");
            if (id) {
              setCurrentId(id);
              // 刚更新的会话必然是最新的那个 → 落在第一页。停在第 2 页会让侧栏
              // 看不到用户当前正在对话的会话。
              setPage(1);
            }
            // L7：截断/降级是「回答不完整」的信号，不能当正常结果静默呈现。
            const truncated = event.truncated === true;
            const degraded = event.degraded === true;
            truncatedNotice = truncated || degraded;
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
            failed = true;
            setError(String(event.message ?? "未知错误"));
          }
        },
        controller.signal,
        // 丢块回调：不写控制台就完了 —— 见下面 dropped 分支的提示。
        () => {
          dropped = true;
        },
      );
    } catch (exc) {
      // 用户主动「中止」不是错误 —— 别把 AbortError 当失败弹给用户。
      // 两种形态都要认：`fetch` 在各运行时里的行为不同（有的让 `reader.read()`
      // 抛 AbortError，有的只是把流转成 done 让整条流安静结束）。
      const aborted =
        controller.signal.aborted ||
        (exc instanceof DOMException && exc.name === "AbortError");
      if (!aborted) {
        failed = true;
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    } finally {
      // 卸载后不写状态（见 alive 的注释）；abortRef 的清理本身无害。
      if (alive.current) {
        setStreaming(false);
      }
      abortRef.current = null;
    }

    // 卸载后到此为止：不再写任何状态，也不再动输入框。
    if (!alive.current) {
      return;
    }

    // 用户中止了本轮（且没等到 done）：半截回答留在屏幕上是对的（他还要看），
    // 但必须说清它**不会被保存** —— 后端对中止的轮次不落库（见 router 的
    // 「整轮跑完才落库」），切走或刷新后内容就没了，不说清楚就像丢数据。
    if (controller.signal.aborted && !finished) {
      if (sawAssistantText.current) {
        setNotice("已中止：这段回答没有保存，刷新或切换会话后不会保留。");
      }
    } else if (!finished && !failed) {
      // 流**安静地**结束了（既没 done 也没 error）：上游/代理把连接正常收尾。
      // 不提示的话，半截回答会被当成本轮正常结果 —— 用户以为助手说完了，
      // 而后端其实没落库（落库发生在 yield done 之前）。这属于「静默截断」。
      setNotice("连接中断：回答可能不完整，且本轮没有保存。");
    } else if (dropped && !truncatedNotice) {
      // 整轮正常结束（收到 done），但中途有 SSE 块没能解析出来 —— 屏幕上的回答
      // 少了那一段，却被当成本轮完整结果呈现。必须点破，否则与上面刚立的
      // 「不许静默截断」规矩自相矛盾。后端已按完整回答落库，所以只提示、
      // **不**把原文还回输入框（还回去反而像「这句没发出去」）。
      //
      // 优先级：**截断/降级提示比「丢块」更严重**（前者说明整段回答本身就不完整，
      // 后者只是屏幕上少了一段），所以 done 已经报了截断时不要再被这条盖掉 ——
      // 反过来（只有丢块、没有截断）仍然要报。
      setNotice("连接异常：有内容未能解析，这段回答可能不完整。");
    }

    // 发言没被保存（失败 / 中止 / 流异常结束）→ 把原文还回输入框。
    //
    // `send()` 第一件事就是 `setInput("")`，而三个失败路径（SSE 的 error 事件、
    // 抛异常、中止）都不恢复：用户想问的那句话从输入框里消失了，而后端**也没存**
    // （失败的轮次不落库）—— 于是只能凭记忆重打一遍。`finished` 为真表示整轮
    // 正常收尾（含截断/降级，那两种也走 done 并已落库），那时不该把旧问题塞回去。
    if (!finished) {
      setInput((current) => (current === "" ? text : current));
    }
  }

  function appendAssistantText(chunk: string) {
    // 同步置位（不经过渲染）—— 中止检查读的就是它，见 sawAssistantText 的注释。
    if (chunk.trim() !== "") {
      sawAssistantText.current = true;
    }
    // 分片先进数组缓冲（push 摊还 O(1)），再一次性 join 出正文 —— 不是每片都做一次
    // `last.content + chunk` 的整串复制（那是 O(n²)）。push 放在 updater **之外**：
    // setLive 的 updater 必须纯净（StrictMode 下会被调用两次，写进去就会翻倍）。
    assistantChunks.current.push(chunk);
    const content = assistantChunks.current.join("");
    setLive((prev) => {
      const last = prev[prev.length - 1];
      // 末尾已是助手气泡就继续追加；否则新开一个。原先这里还判了 `!last.sealed`，
      // 而 `sealed` 全仓没有任何写入点（恒为 undefined）—— 死字段，
      // 留着会让人以为存在「封口后另起气泡」的行为。已删。
      if (last && last.role === "assistant") {
        return [...prev.slice(0, -1), { ...last, content }];
      }
      return [...prev, { role: "assistant", content }];
    });
  }

  /** 切会话 / 新对话：本轮身份作废（不 reset 会把上一轮的判据套到新会话上）。 */
  function resetTurn(): void {
    setTurn(null);
    setTurnSettled(false);
  }

  return (
    // 窄屏堆叠（会话列表在上、对话区在下），宽屏恢复左侧栏。
    // 224px 的固定侧栏在 390px 窗口里会吃掉近六成宽度。
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      <aside className="md:w-56 md:shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-ink-soft">会话</h2>
          <Button
            disabled={streaming}
            onClick={() => {
              setCurrentId(null);
              setLive([]);
              resetTurn();
              setError(null);
              setNotice(null);
            }}
          >
            新对话
          </Button>
        </div>
        {/* 会话列表的加载/错误态：读失败与「没有会话」必须在视觉上分得开 ——
            空列表看起来就是「一条会话都没有」，用户不会想到去重试（对齐
            ApplicationsPage 对「失败 ≠ 空」的处理）。firstLoad 才显示加载态；
            翻页有 placeholderData 兜底，不会闪。 */}
        {conversations.isLoading && <p className="px-2 text-sm text-muted">加载会话列表…</p>}
        {conversations.isError && (
          <div className="px-2">
            <ErrorNotice message={(conversations.error as Error).message} />
          </div>
        )}
        <ul className="space-y-1">
          {(conversations.data?.items ?? []).map((item) => (
            <li key={item.id}>
              {/* 流式进行中禁止切换：切走会把本轮流内容追加到另一个会话上，
                  且 done 后 currentId 被覆盖回去 —— 用户的选择被静默撤销。 */}
              <button
                type="button"
                disabled={streaming}
                className={`w-full truncate rounded px-2 py-1 text-left text-sm ${
                  currentId === item.id ? "bg-ink text-white" : "hover:bg-surface-sunken"
                } ${streaming ? "cursor-not-allowed opacity-50" : ""}`}
                onClick={() => {
                  setCurrentId(item.id);
                  setLive([]);
                  resetTurn();
                  setError(null);
                  setNotice(null);
                }}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>

        {/* 列表是**分页**的（后端 `page_size` 上限 50）：有下一页或不在首页时才出现
            总数与分页控件。原先只有一句「还有更多」却没有翻页入口 —— 第 51 个起的
            会话不只是不可见，而是**无法触达**（数据在那儿，没有任何办法取出来）。
            会话不多时这里什么都不渲染（Pager 在首页且无下一页时自己返回 null），
            不占地方也不制造假警报。 */}
        {conversations.data && (conversations.data.has_next || page > 1) && (
          <div className="mt-2 px-2">
            <p className="text-xs text-muted">共 {conversations.data.total} 个会话</p>
            <Pager
              ariaLabel="会话分页"
              className="mt-1 flex-wrap"
              page={page}
              hasNext={conversations.data.has_next}
              busy={conversations.isPlaceholderData}
              onPageChange={setPage}
            />
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="求职助手" subtitle="只读：它查岗位与投递、给建议；写操作由你确认" />

        {/* 消息区必须有一个**确定的高度上限**，否则 `overflow-y-auto` 永不触发：
            祖先链里没有任何确定高度（`min-h-screen` 给不了 flex item 有界高度），
            对话一长输入框就被顶到屏幕外，用户得滚动页面才能继续提问。`max-h-[60vh]`
            是**确定值**（视口高度），不依赖祖先布局。 */}
        <Card className="max-h-[60vh] overflow-y-auto p-4">
          {/* M8 的历史加载/错误提示只**顶替空态**，绝不能盖住消息列表：
              `items` 里含本轮的 live 消息（历史请求失败时更是只有 live）。
              把整块换成提示，会让用户刚发出的问题与正在流式输出的回答凭空消失
              —— 后端其实已经落库，看起来却像「消息丢了」。 */}
          {historyError && (
            <div className="mb-3">
              <ErrorNotice message={`加载会话历史失败：${(messages.error as Error).message}`} />
            </div>
          )}
          {historyLoading && <p className="mb-3 text-sm text-muted">加载会话历史…</p>}
          {items.length > 0 && (
            // `role="log"` + `aria-live="polite"`：新消息与流式增量的到达要能被
            // 屏幕阅读器播报（`log` 是聊天记录的语义角色，只播报新增内容）。
            // `aria-busy` 在流式期间为真，避免把逐字增量当成一串独立消息播报。
            <div className="space-y-3" role="log" aria-live="polite" aria-busy={streaming}>
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
          <p className="mt-3 text-sm text-warning">{notice}</p>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <input
            className={`flex-1 ${fieldClass}`}
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
