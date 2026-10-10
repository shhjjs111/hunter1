import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScoreView } from "../api";
import { ScoreCard } from "./ScoreCard";

const CARD: ScoreView = {
  job_id: "j1",
  score: 77,
  summary: "总体匹配",
  advantages: "有 LLM 落地经验",
  gaps: "缺大规模团队经验",
  model: "fake",
  prompt_version: "v3",
  scored_at: null,
};

describe("ScoreCard", () => {
  it("把模型的结论摆出来，而不是只说一句「已评分」", () => {
    render(<ScoreCard card={CARD} />);
    expect(screen.getByText("匹配分 77")).toBeTruthy();
    expect(screen.getByText("总体匹配")).toBeTruthy();
    expect(screen.getByText("有 LLM 落地经验")).toBeTruthy();
    expect(screen.getByText("缺大规模团队经验")).toBeTruthy();
  });

  it("模型只给分数时要说出来，而不是渲染一个空框", () => {
    render(<ScoreCard card={{ ...CARD, summary: null, advantages: null, gaps: null }} />);
    expect(screen.getByText(/没有写结论/)).toBeTruthy();
  });
});
