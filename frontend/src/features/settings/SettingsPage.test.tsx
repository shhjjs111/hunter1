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
  broken: false,
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
  it("用户改过的字段不会被 refetch 偷偷填回服务器旧值", async () => {
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
    const baseUrl = (await screen.findByLabelText(/Base URL/)) as HTMLInputElement;
    await waitFor(() => {
      expect(baseUrl.value).toBe("https://api.example.com/v1");
    });

    // 用户改成别的地址。注：原先这个用例用 temperature 做载体（「清空一个可选字段
    // 后表单仍可提交」），但 temperature / max_tokens 已作为「存而不用」的死控件
    // 撤除，表单里只剩 base_url / model 两个必填项 —— 清空它们会被 HTML5 required
    // 挡住提交，复现不出 refetch。于是改用「改成一个不同的非空值」：它同样能锁住
    // 「hydrated 守卫是否还在」（守卫若被删，refetch 会把服务器旧值盖回去）。
    fireEvent.change(baseUrl, { target: { value: "https://typed.example.com/v1" } });
    expect(baseUrl.value).toBe("https://typed.example.com/v1");

    // 保存会 invalidate ["settings"] → refetch。
    // 用 masked_key 当「refetch 已落地」的锚点 —— 它渲染在 API Key 的提示里。
    // 没有这个锚点，waitFor 会在 refetch 完成前就满足（值本来就是），
    // 测试等于什么都没验（第一版就是这么假绿的）。
    payload = { ...SETTINGS, masked_key: "sk-…REFETCHED" };
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await screen.findByText(/sk-…REFETCHED/);

    // 关键：用户刚改的值不许被异步回填覆盖
    expect((screen.getByLabelText(/Base URL/) as HTMLInputElement).value).toBe(
      "https://typed.example.com/v1",
    );
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

  it("保存成功后才清空 API Key 输入框", async () => {    vi.stubGlobal(
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

  it("配置损坏时：说明原因，且表单可填（能自救）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          base_url: "",
          model: "",
          masked_key: "",
          configured: false,
          broken: true,
        }),
      ),
    );

    renderPage();

    // 不静默：必须说清「已保存的配置不可用」，而不是当成「还没配过」
    expect(await screen.findByText(/已保存的模型配置不合法/)).toBeDefined();
    // 不死锁：表单仍在，用户重填即可覆盖
    expect(screen.getByLabelText(/Base URL/)).toBeDefined();
    expect(screen.getByRole("button", { name: "保存" })).toBeDefined();
  });
});
