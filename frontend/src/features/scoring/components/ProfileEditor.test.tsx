import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfileEditor, splitLines } from "./ProfileEditor";

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderEditor() {
  return render(<ProfileEditor />, { wrapper });
}

describe("splitLines", () => {
  it("按行切分并去掉空行与首尾空白", () => {
    expect(splitLines("  a \n\n b \n  ")).toEqual(["a", "b"]);
  });
});

describe("ProfileEditor", () => {
  it("未配画像时说明「还没配」，而不是显示一个像配过的空表单", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ profile: null })),
    );
    renderEditor();
    expect(await screen.findByText(/还没配画像/)).toBeDefined();
  });

  it("已配画像时回填到表单", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          profile: { keywords: ["AI产品经理"], directions: ["大模型"], summary: "三年经验" },
        }),
      ),
    );
    renderEditor();
    const keywords = (await screen.findByLabelText(/目标关键词/)) as HTMLTextAreaElement;
    expect(keywords.value).toBe("AI产品经理");
    expect(screen.getByDisplayValue("三年经验")).toBeDefined();
  });

  it("保存把多行拆成列表提交", async () => {
    // openapi-fetch 以 Request 对象调用 fetch（method 在对象上，无第二参数）
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      (input as Request).method === "PUT"
        ? jsonResponse({ profile: { keywords: ["a", "b"] } })
        : jsonResponse({ profile: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderEditor();
    fireEvent.change(await screen.findByLabelText(/目标关键词/), { target: { value: "a\nb" } });
    fireEvent.click(screen.getByRole("button", { name: /保存画像/ }));

    const put = await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) => (input as Request).method === "PUT");
      expect(call).toBeDefined();
      return call!;
    });
    const body = await (put[0] as Request).clone().json();
    expect(body.keywords).toEqual(["a", "b"]);
  });

  it("后端 422 的可读原因原样透出（不替换成笼统的「保存失败」）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        (input as Request).method === "PUT"
          ? jsonResponse({ detail: "画像至少要有一项信号" }, 422)
          : jsonResponse({ profile: null }),
      ),
    );
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /保存画像/ }));
    expect(await screen.findByText(/画像至少要有一项信号/)).toBeDefined();
  });
});
