import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { makeClient, SCORE, stubApi } from "../../test-utils";
import { useScoreJob } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("重评结果立即更新评分查询缓存，重新打开详情不再显示旧结论", async () => {
  stubApi();
  const client = makeClient();
  client.setQueryData(["scoring", "score", "j2"], { ...SCORE, score: 20, summary: "旧结论" });
  const { result } = renderHook(() => useScoreJob(), {
    wrapper: ({ children }: { children: ReactNode }) =>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
  await act(async () => { await result.current.mutateAsync("j2"); });
  expect(client.getQueryData(["scoring", "score", "j2"])).toEqual(SCORE);
});
