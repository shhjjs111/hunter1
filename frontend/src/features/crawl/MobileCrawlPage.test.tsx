import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CRAWL_IDLE, jsonResponse, renderWithChrome, stubApi, stubMedia } from "../../test-utils";
import { MobileCrawlPage } from "./MobileCrawlPage";

beforeEach(() => {
  stubMedia({ reduced: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileCrawlPage", () => {
  it("展示统计、站点状态与失败原因", async () => {
    stubApi();
    renderWithChrome(<MobileCrawlPage />);
    expect(await screen.findByText("实习僧")).toBeTruthy();
    expect(screen.getByText("本轮共抓到 12 条 · 1 个站点失败")).toBeTruthy();
    expect(screen.getByText("完成")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("风控页")).toBeTruthy();
    expect(screen.getByRole("button", { name: "再抓一轮" })).toBeTruthy();
  });

  it("开始抓取成功后提示", async () => {
    const fetchMock = stubApi();
    const { chrome } = renderWithChrome(<MobileCrawlPage />);
    fireEvent.click(await screen.findByRole("button", { name: "再抓一轮" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已开始抓取。"));
    expect(
      fetchMock.mock.calls.some(
        ([input]) => (input as Request).method === "POST" && (input as Request).url.endsWith("/api/crawl"),
      ),
    ).toBe(true);
  });

  it("后端说上一轮还在跑时不谎报已开始", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/crawl" ? jsonResponse({ started: false }) : undefined,
    );
    const { chrome } = renderWithChrome(<MobileCrawlPage />);
    fireEvent.click(await screen.findByRole("button", { name: "再抓一轮" }));
    await waitFor(() => expect(chrome.showToast).not.toHaveBeenCalled());
  });

  it("启动失败时内联显示原因并弹错误 Toast", async () => {
    stubApi((request, url) =>
      url.pathname === "/api/crawl" && request.method === "POST"
        ? jsonResponse({ detail: "抓取线程启动失败" }, 500)
        : undefined,
    );
    const { chrome } = renderWithChrome(<MobileCrawlPage />);
    fireEvent.click(await screen.findByRole("button", { name: "再抓一轮" }));
    expect(await screen.findByText(/启动抓取失败/)).toBeTruthy();
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("error", expect.stringContaining("启动抓取失败")));
  });

  it("读取状态失败时显示错误", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/crawl/status" ? jsonResponse({ detail: "boom" }, 500) : undefined,
    );
    renderWithChrome(<MobileCrawlPage />);
    expect(await screen.findByText("读取失败")).toBeTruthy();
    expect(await screen.findByText(/读取抓取进度失败/)).toBeTruthy();
  });

  it("还没跑过抓取时给出空状态", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/crawl/status"
        ? jsonResponse({ ...CRAWL_IDLE, sites: [], total_fetched: 0, total_created: 0 })
        : undefined,
    );
    renderWithChrome(<MobileCrawlPage />);
    expect(await screen.findByText(/还没有跑过抓取/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "开始抓取" })).toBeTruthy();
  });
});
