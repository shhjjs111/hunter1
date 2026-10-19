import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithChrome, stubApi, stubMedia } from "../../test-utils";
import { MobileApplicationsPage } from "./MobileApplicationsPage";

beforeEach(() => {
  stubMedia({ reduced: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileApplicationsPage", () => {
  it("展示投递记录、阶段数量与面试待跟进数", async () => {
    stubApi();
    renderWithChrome(<MobileApplicationsPage />);
    expect(await screen.findByText("字节跳动")).toBeTruthy();
    expect(screen.getByText("月之暗面")).toBeTruthy();
    expect(screen.getByText("共 2 条 · 1 场面试待跟进")).toBeTruthy();
    expect(screen.getByRole("button", { name: /面试/ })).toBeTruthy();
  });

  it("按阶段筛选", async () => {
    stubApi();
    renderWithChrome(<MobileApplicationsPage />);
    await screen.findByText("字节跳动");
    fireEvent.click(screen.getByRole("button", { name: /^面试/ }));
    expect(screen.queryByText("字节跳动")).toBeNull();
    expect(screen.getByText("月之暗面")).toBeTruthy();
  });

  it("Action Sheet 改阶段并提示", async () => {
    const fetchMock = stubApi();
    const { chrome } = renderWithChrome(<MobileApplicationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "修改月之暗面的投递阶段" }));

    const sheet = await screen.findByRole("dialog");
    fireEvent.click(within(sheet).getByRole("button", { name: /Offer/ }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已改为「Offer」"));
    expect(
      fetchMock.mock.calls.some(
        ([input]) =>
          (input as Request).method === "POST" && (input as Request).url.endsWith("/api/applications/app2/stage"),
      ),
    ).toBe(true);
  });

  it("删除必须经二次确认，取消不发请求", async () => {
    const fetchMock = stubApi();
    const { chrome } = renderWithChrome(<MobileApplicationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "修改月之暗面的投递阶段" }));
    fireEvent.click(await screen.findByRole("button", { name: /删除这条记录/ }));

    const confirm = await screen.findByRole("alertdialog");
    expect(within(confirm).getByText(/删除「月之暗面」的投递记录/)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(fetchMock.mock.calls.some(([input]) => (input as Request).method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "修改月之暗面的投递阶段" }));
    fireEvent.click(await screen.findByRole("button", { name: /删除这条记录/ }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已删除"));
    expect(
      fetchMock.mock.calls.some(
        ([input]) =>
          (input as Request).method === "DELETE" && (input as Request).url.endsWith("/api/applications/app2"),
      ),
    ).toBe(true);
  });

  it("备注失焦后保存", async () => {
    const fetchMock = stubApi();
    const { chrome } = renderWithChrome(<MobileApplicationsPage />);
    const note = await screen.findByRole("textbox", { name: "字节跳动的备注" });
    fireEvent.change(note, { target: { value: "约了下周一面" } });
    fireEvent.blur(note);
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "备注已保存。"));
    expect(
      fetchMock.mock.calls.some(
        ([input]) =>
          (input as Request).method === "POST" && (input as Request).url.endsWith("/api/applications/app1/stage"),
      ),
    ).toBe(true);
  });

  it("读取失败时显示错误", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/applications" ? jsonResponse({ detail: "boom" }, 500) : undefined,
    );
    renderWithChrome(<MobileApplicationsPage />);
    expect(await screen.findByText(/加载投递记录失败/)).toBeTruthy();
    expect(screen.getByText("读取失败")).toBeTruthy();
  });
});
