import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithChrome, stubApi, stubMedia } from "../../test-utils";
import { MobileJobsPage } from "./MobileJobsPage";

beforeEach(() => {
  stubMedia({ reduced: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileJobsPage", () => {
  it("加载岗位列表并展示统计副标题", async () => {
    stubApi();
    renderWithChrome(<MobileJobsPage />);
    expect(await screen.findByText("AI产品经理")).toBeTruthy();
    expect(screen.getByText("共 2 条 · 1 条未评分")).toBeTruthy();
    expect(screen.getByText("大模型应用工程师")).toBeTruthy();
  });

  it("筛选 Chip 只保留对应岗位", async () => {
    stubApi();
    renderWithChrome(<MobileJobsPage />);
    await screen.findByText("AI产品经理");

    fireEvent.click(screen.getByRole("button", { name: "未评分" }));
    expect(screen.queryByText("AI产品经理")).toBeNull();
    expect(screen.getByText("大模型应用工程师")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "80 分以上" }));
    expect(screen.getByText("AI产品经理")).toBeTruthy();
    expect(screen.queryByText("大模型应用工程师")).toBeNull();
  });

  it("搜索输入 300ms 防抖后带上关键词请求", async () => {
    const fetchMock = stubApi();
    renderWithChrome(<MobileJobsPage />);
    await screen.findByText("AI产品经理");

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索岗位" }), {
      target: { value: "大模型" },
    });

    await waitFor(
      () => {
        const searched = fetchMock.mock.calls.some(([input]) => {
          const url = (input as Request).url;
          return url.includes("/api/jobs") && url.includes("q=%E5%A4%A7%E6%A8%A1%E5%9E%8B");
        });
        expect(searched).toBe(true);
      },
      { timeout: 1500 },
    );
  });

  it("记录投递后按钮变为已投递并提示", async () => {
    const fetchMock = stubApi();
    const { chrome } = renderWithChrome(<MobileJobsPage />);
    await screen.findByText("大模型应用工程师");

    fireEvent.click(screen.getByRole("button", { name: "记录投递：大模型应用工程师" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已记录投递。"));
    expect(
      await screen.findByRole("button", { name: "已投递：大模型应用工程师" }),
    ).toHaveProperty("disabled", true);

    const post = fetchMock.mock.calls.find(
      ([input]) => (input as Request).method === "POST" && (input as Request).url.endsWith("/api/applications"),
    );
    expect(post).toBeTruthy();
  });

  it("详情 Sheet：加载 JD、评分后展示结论并可记录投递", async () => {
    stubApi();
    const { chrome } = renderWithChrome(<MobileJobsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /大模型应用工程师，月之暗面，查看详情/ }));

    const dialog = await screen.findByRole("dialog", { name: "大模型应用工程师 详情" });
    expect(within(dialog).getByText("还没有评分")).toBeTruthy();
    expect(await within(dialog).findByText("负责大模型产品规划与落地。")).toBeTruthy();
    expect(chrome.setOverlayOpen).toHaveBeenCalledWith(true);

    fireEvent.click(within(dialog).getByRole("button", { name: "评分" }));
    expect(await within(dialog).findByRole("img", { name: "匹配分 91" })).toBeTruthy();
    expect(within(dialog).getByText("匹配度高")).toBeTruthy();
    expect(within(dialog).getByText("产品经验对口")).toBeTruthy();
    expect(within(dialog).getByText("缺少大模型落地案例")).toBeTruthy();
    expect(chrome.showToast).toHaveBeenCalledWith("success", "已评分：91 分");

    fireEvent.click(within(dialog).getByRole("button", { name: "记录投递" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已记录投递。"));
  });

  it("空列表引导去抓取", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/jobs" ? jsonResponse({ items: [], total: 0, page: 1, page_size: 20, has_next: false }) : undefined,
    );
    const { chrome } = renderWithChrome(<MobileJobsPage />);
    expect(await screen.findByText(/没有找到岗位/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "去抓取" }));
    expect(chrome.animateTo).toHaveBeenCalledWith(1);
  });

  it("读取失败时显示可读错误", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/jobs" ? jsonResponse({ detail: "boom" }, 500) : undefined,
    );
    renderWithChrome(<MobileJobsPage />);
    expect(await screen.findByText(/加载岗位失败/)).toBeTruthy();
    expect(screen.getByText("读取失败")).toBeTruthy();
  });

  it("抓取进行中时顶部状态条可跳到抓取页", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/crawl/status"
        ? jsonResponse({
            running: true,
            started_at: null,
            finished_at: null,
            error: null,
            sites: [
              { label: "实习僧", status: "ok", fetched: 2, created: 1, updated: 0, error: null },
              { label: "Boss直聘", status: "running", fetched: 0, created: 0, updated: 0, error: null },
            ],
            total_fetched: 2,
            total_created: 1,
          })
        : undefined,
    );
    const { chrome } = renderWithChrome(<MobileJobsPage />);
    const live = await screen.findByRole("button", { name: /抓取中/ });
    fireEvent.click(live);
    expect(chrome.animateTo).toHaveBeenCalledWith(1);
  });
});
