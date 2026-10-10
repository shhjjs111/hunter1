import type { ScoreView } from "../api";

/**
 * 一次评分的结论卡片：分数 + 摘要 + 优势 + 差距。
 *
 * 这三段文字是**模型算出来的**。原先 `advantages` / `gaps` 连响应都没进、`summary`
 * 也只是在提示条里闪一下就没了 —— 用户看不到「为什么是这个分」，而模型已经为此
 * 花过钱。后端现在把它们落库并随响应原样返回，这里负责让它们**看得见**。
 *
 * 模型只给分数、不写结论是**合法**的（`score` 是唯一必填项），所以那种情况要说出来，
 * 而不是渲染一个空框让人以为是界面坏了。
 */
export function ScoreCard({ card }: { card: ScoreView }) {
  const sections: Array<{ label: string; text: string }> = [];
  if (card.summary) sections.push({ label: "摘要", text: card.summary });
  if (card.advantages) sections.push({ label: "优势", text: card.advantages });
  if (card.gaps) sections.push({ label: "差距", text: card.gaps });

  return (
    <section className="rounded border border-line bg-surface p-3 text-sm">
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="font-semibold">匹配分 {card.score}</span>
        {card.model && <span className="text-xs text-muted">模型：{card.model}</span>}
        {card.prompt_version && (
          <span className="text-xs text-muted">提示词：{card.prompt_version}</span>
        )}
      </header>
      {sections.length === 0 ? (
        <p className="mt-2 text-muted">模型这次只给了分数，没有写结论。</p>
      ) : (
        <dl className="mt-2 space-y-2">
          {sections.map(({ label, text }) => (
            <div key={label}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="whitespace-pre-wrap">{text}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
