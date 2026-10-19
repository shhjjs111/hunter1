import { useEffect, useState, type CSSProperties } from "react";
import { useMediaQuery } from "../../../app/useMediaQuery";

/** 评分环：conic-gradient + 数字滚动（900ms；减少动态效果时直接到终点）。 */
export function ScoreRing({ score, size = 66 }: { score: number | null; size?: number }) {
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [animated, setAnimated] = useState<{ score: number; value: number } | null>(null);

  useEffect(() => {
    if (score === null || reduced) {
      return;
    }
    const started = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / 900);
      const eased = 1 - (1 - progress) ** 3;
      setAnimated({ score, value: Math.round(score * eased) });
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      }
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [reduced, score]);

  const color = score === null ? "var(--color-subtle)" : score >= 80 ? "var(--color-success)" : "#ff9f0a";
  const shownValue = score === null ? 0 : reduced ? score : animated?.score === score ? animated.value : 0;
  const percent = Math.max(0, Math.min(100, shownValue));
  const shown = score === null ? "—" : shownValue;
  return (
    <div
      className="mobile-score-ring"
      role="img"
      aria-label={score === null ? "还没有评分" : `匹配分 ${score}`}
      style={
        {
          width: size,
          height: size,
          "--ring-color": color,
          "--ring-percent": percent,
        } as CSSProperties
      }
    >
      <b>{shown}</b>
    </div>
  );
}
