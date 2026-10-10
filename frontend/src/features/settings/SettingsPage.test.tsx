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
  it("加载期间用户先敲进去的内容不会被回填覆盖", async () => {
    // 慢网络下「页面一打开就开始输入」是常态。回填时若无视用户已经动过的字段，
    // 刚敲的地址会在响应到达那一刻被服务端旧值吃掉。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        await gate;
        return jsonResponse(SETTINGS);
      }),
    );

    renderPage();
    const baseUrl = (await screen.findByLabelText(/Base URL/)) as HTMLInputElement;
    fireEvent.change(baseUrl, { target: { value: "https://typed.example.com/v1" } });

    release();
    // 等回填确实发生过（没被用户动过的 model 被填上服务器值）……
    const model = (await screen.findByLabelText(/模型/)) as HTMLInputElement;
    await waitFor(() => expect(model.value).toBe("deepseek-chat"));
    // ……同时用户敲过的 base_url 保持原样
    expect(baseUrl.value).toBe("https://typed.example.com/v1");
  });

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

  it("「测试连接」用表单当前值，而不是已保存的配置", async () => {
    // 回归护栏：原先点「测试连接」命中的是**服务端已保存的配置**（端点不带请求体）。
    // 用户改了输入框再点测试，测的还是旧配置 —— 他以为验的是眼前这份，实际不是。
    const probeBodies: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        if (req.method === "POST") {
          probeBodies.push(await req.clone().text());
          return jsonResponse({ ok: true, message: "连接成功" });
        }
        return jsonResponse(SETTINGS);
      }),
    );

    renderPage();
    const baseUrl = (await screen.findByLabelText(/Base URL/)) as HTMLInputElement;
    await waitFor(() => expect(baseUrl.value).toBe("https://api.example.com/v1"));

    // 用户改了地址与模型，但**没有保存**
    fireEvent.change(baseUrl, { target: { value: "https://typed.example.com/v1" } });
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "deepseek-reasoner" } });
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));

    await screen.findByText(/连接成功/);

    expect(probeBodies).toHaveLength(1);
    expect(probeBodies[0]).toContain("https://typed.example.com/v1");
    expect(probeBodies[0]).toContain("deepseek-reasoner");
  });

  it("开始一次操作时清掉上一次**另一类**操作的提示（不许两条同屏）", async () => {
    // 实测（修复前）：① 点「测试连接」失败 → 出现探测错误；② 改个非法地址点「保存」
    // → 保存错误出现，而**旧那条探测错误还留在屏幕上**（mutation 的状态不会自己消失）。
    // 两条红条并排，用户分不出哪条对应当下这次，而旧那条说的还是上一轮的输入。
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        if (req.method === "POST") {
          return jsonResponse({ ok: false, message: "探测失败：连不上端点" });
        }
        if (req.method === "PUT") {
          return jsonResponse({ detail: "base_url 必须是 http(s) 地址" }, 422);
        }
        return jsonResponse(SETTINGS);
      }),
    );

    renderPage();
    await screen.findByLabelText(/Base URL/);

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText(/探测失败/);

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText(/http\(s\)/);

    expect(screen.queryByText(/探测失败/)).toBeNull();
  });
});
