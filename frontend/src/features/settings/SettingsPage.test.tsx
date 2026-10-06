import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SETTINGS = {
  base_url: "https://api.example.com/v1",
  model: "deepseek-chat",
  masked_key: "sk-…abcd",
  configured: true,
  temperature: 0.7,
  max_tokens: null,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SettingsPage", () => {
  it("用户清空某字段后，refetch 不会偷偷填回服务器旧值", async () => {
    let payload: unknown = SETTINGS;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        if (req.method === "PUT") {
          return jsonResponse(SETTINGS);
        }
        return jsonResponse(payload);
      }),
    );

    renderPage();
    const temperature = (await screen.findByLabelText(/temperature/)) as HTMLInputElement;
    await waitFor(() => {
      expect(temperature.value).toBe("0.7");
    });

    // 用户清空（temperature 是可选字段，清空后表单仍可提交 ——
    // 用必填的 base_url 复现不出来：HTML5 required 会直接挡住提交）
    fireEvent.change(temperature, { target: { value: "" } });
    expect(temperature.value).toBe("");

    // 保存会 invalidate ["settings"] → refetch。
    // 用 masked_key 当「refetch 已落地」的锚点 —— 它渲染在 API Key 的提示里。
    // 没有这个锚点，waitFor 会在 refetch 完成前就满足（值本来就是 ""），
    // 测试等于什么都没验（第一版就是这么假绿的）。
    payload = { ...SETTINGS, masked_key: "sk-…REFETCHED" };
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await screen.findByText(/sk-…REFETCHED/);

    // 关键：用户刚清空的字段不许被异步回填覆盖
    expect((screen.getByLabelText(/temperature/) as HTMLInputElement).value).toBe("");
  });

  it("保存失败时不清空 API Key 输入框（否则用户要重敲密钥）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        if (req.method === "PUT") {
          return jsonResponse({ detail: "模型名不合法" }, 422);
        }
        return jsonResponse(SETTINGS);
      }),
    );

    renderPage();
    const apiKey = (await screen.findByLabelText(/API Key/)) as HTMLInputElement;
    fireEvent.change(apiKey, { target: { value: "sk-secret-typed-by-user" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    // 等失败提示出现，确认请求确实失败了
    await screen.findByText(/模型名不合法/);

    // 失败时密钥不能被清掉 —— 清掉等于让用户重敲一遍
    expect((screen.getByLabelText(/API Key/) as HTMLInputElement).value).toBe(
      "sk-secret-typed-by-user",
    );
  });

  it("保存成功后才清空 API Key 输入框", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        if (req.method === "PUT") {
          return jsonResponse(SETTINGS);
        }
        return jsonResponse(SETTINGS);
      }),
    );

    renderPage();
    const apiKey = (await screen.findByLabelText(/API Key/)) as HTMLInputElement;
    fireEvent.change(apiKey, { target: { value: "sk-new" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect((screen.getByLabelText(/API Key/) as HTMLInputElement).value).toBe("");
    });
  });
});
