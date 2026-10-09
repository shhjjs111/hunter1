import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import contract from "../../../../../contracts/openapi.json";
import {
  MAX_DIRECTIONS,
  MAX_ITEM_CHARS,
  MAX_KEYWORDS,
  MAX_SUMMARY_CHARS,
  ProfileEditor,
  splitLines,
} from "./ProfileEditor";

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

describe("前端上限与契约一致", () => {
  // 后端是闸门，前端这四个常量只是提前告知 —— 但它们是**复制**来的，会漂移。
  // 漂移的后果不对称：前端比后端松 → 用户白填一遍才吃 422；
  //                前端比后端紧 → 用户被界面挡住，而后端其实能收。
  // 所以拿契约快照逐条比对，而不是靠人工记得同步。
  const props = (
    contract as {
      components: {
        schemas: {
          CandidateProfile: {
            properties: Record<
              string,
              { maxItems?: number; maxLength?: number; items?: { maxLength?: number } }
            >;
          };
        };
      };
    }
  ).components.schemas.CandidateProfile.properties;
  it("条数与字符上限逐条对齐", () => {
    expect(props.keywords.maxItems).toBe(MAX_KEYWORDS);
    expect(props.directions.maxItems).toBe(MAX_DIRECTIONS);
    expect(props.keywords.items?.maxLength).toBe(MAX_ITEM_CHARS);
    expect(props.directions.items?.maxLength).toBe(MAX_ITEM_CHARS);
    expect(props.summary.maxLength).toBe(MAX_SUMMARY_CHARS);
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

  it("提示里写明「每条不超过 100 字符」—— 单条超限也会被后端拒", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ profile: null })),
    );
    renderEditor();
    // 只写「最多 50 条」是不够的：用户填一条 300 字符的关键词照样吃 422。
    // 关键词与方向两处都要写（故用 findAll，且断言恰好两处）。
    const hints = await screen.findAllByText(/每条不超过 100 字符/);
    expect(hints).toHaveLength(2);
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

  it("存储里的画像不可用时：说明原因，且表单仍可填（能修）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ profile: null, warning: "已保存的画像不可用，请重新填写：太长" }),
      ),
    );
    renderEditor();
    // 不静默：必须把损坏原因说出来
    expect(await screen.findByText(/已保存的画像不可用/)).toBeDefined();
    // 不死锁：表单仍在，用户能重填覆盖
    expect(screen.getByLabelText(/目标关键词/)).toBeDefined();
  });

  it("超限的 422 原因（含具体上限）原样透出", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        (input as Request).method === "PUT"
          ? jsonResponse({ detail: "画像不合法：至少要有一项信号…单条 100 字符…" }, 422)
          : jsonResponse({ profile: null }),
      ),
    );
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /保存画像/ }));
    expect(await screen.findByText(/单条 100 字符/)).toBeDefined();
  });
});

describe("ProfileEditor 读取失败", () => {
  /**
   * 回归护栏：读取失败时**不得**整块早返回把表单卸载。
   *
   * 三个 textarea 是**非受控**的（`defaultValue`），一旦卸载，用户敲进去、还没保存
   * 的内容就随组件一起消失，屏幕上只剩一条错误提示。而全局 `staleTime` 30 秒 +
   * `retry: 1`：编辑到一半切走窗口再切回、一次后台 refetch 失败就会触发。
   *
   * 隔壁 `SettingsPage` 的注释把同一个教训写得很清楚（回填不许盖住用户刚敲的内容），
   * 那里改用派生值 + 内联错误提示避开了。这里对齐同一取舍。
   */
  it("编辑中 refetch 失败不吃掉已输入的内容（表单不卸载）", async () => {
    let failing = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (failing) {
          return jsonResponse({ detail: "后端暂时不可用" }, 503);
        }
        return jsonResponse({
          profile: { keywords: ["旧关键词"], directions: [], summary: "旧摘要" },
        });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ProfileEditor />
      </QueryClientProvider>,
    );

    const keywords = (await screen.findByLabelText(/目标关键词/)) as HTMLTextAreaElement;
    await waitFor(() => expect(keywords.value).toBe("旧关键词"));

    // 用户开始编辑（还没保存）
    fireEvent.change(keywords, { target: { value: "我敲了一半" } });
    expect(keywords.value).toBe("我敲了一半");

    // 下一次读取失败 —— 模拟窗口重新聚焦 / 保存后失效触发的后台 refetch
    failing = true;
    await client.refetchQueries({ queryKey: ["scoring", "profile"] });

    // 不静默：错误必须说出来
    expect(await screen.findByText(/后端暂时不可用|加载画像失败/)).toBeDefined();
    // 关键：表单没被卸载，用户敲进去的内容仍在
    expect((screen.getByLabelText(/目标关键词/) as HTMLTextAreaElement).value).toBe("我敲了一半");
  });
});
