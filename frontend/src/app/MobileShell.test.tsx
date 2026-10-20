import "fake-indexeddb/auto";

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appRoutes } from "./App";
import { applyInitialTheme } from "./theme";
import { jsonResponse, renderRoutes, stubApi, stubMedia } from "../test-utils";
import { maybeAutoCrawl } from "../mobile/localApi";
import { resetDBForTests } from "../mobile/db";

const mobileMock = vi.hoisted(() => ({ native: false }));

vi.mock("../mobile", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../mobile")>();
  return { ...actual, isMobileApp: () => mobileMock.native };
});

vi.mock("../mobile/localApi", () => ({
  maybeAutoCrawl: vi.fn().mockResolvedValue(false),
}));

beforeEach(async () => {
  await resetDBForTests();
  mobileMock.native = false;
  vi.mocked(maybeAutoCrawl).mockClear();
  stubMedia({ reduced: true, mobile: true });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  Element.prototype.scrollTo = vi.fn();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  applyInitialTheme();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

function dock() {
  return screen.getByRole("navigation", { name: "主导航" });
}

describe("MobileShell", () => {
  it("原生平台打开和回到前台会检查每日补抓", async () => {
    stubApi();
    mobileMock.native = true;
    const auto = vi.mocked(maybeAutoCrawl);
    renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");
    await waitFor(() => expect(auto).toHaveBeenCalledTimes(1));

    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(auto.mock.calls.length).toBeGreaterThan(1));
    const beforeFocus = auto.mock.calls.length;
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(auto.mock.calls.length).toBeGreaterThan(beforeFocus));
  });

  it("渲染五个常驻页面与 Dock，非当前页不可聚焦", async () => {
    stubApi();
    const { container } = renderRoutes(appRoutes, "/");
    expect(await screen.findByText("AI产品经理")).toBeTruthy();

    const nav = dock();
    for (const label of ["岗位库", "抓取", "投递记录", "求职助手", "配置"]) {
      expect(within(nav).getByRole("button", { name: label })).toBeTruthy();
    }

    const pages = container.querySelectorAll("section.mobile-page");
    expect(pages).toHaveLength(5);
    expect(pages[0].getAttribute("aria-hidden")).toBeNull();
    expect(pages[1].getAttribute("aria-hidden")).toBe("true");
    expect(pages[1].hasAttribute("inert")).toBe(true);
    expect(pages[0].hasAttribute("inert")).toBe(false);
    expect(container.querySelector(".mobile-center-title")?.textContent).toBe("岗位库");
  });

  it("点 Dock 切页、更新 URL 与顶栏标题", async () => {
    stubApi();
    const { router } = renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");

    fireEvent.click(within(dock()).getByRole("button", { name: "抓取" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/crawl"));
    expect(await screen.findByRole("heading", { name: "抓取" })).toBeTruthy();
    expect(document.querySelector(".mobile-center-title")?.textContent).toBe("抓取");

    fireEvent.click(within(dock()).getByRole("button", { name: "配置" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings"));
    expect(await screen.findByRole("heading", { name: "配置" })).toBeTruthy();
  });

  it("左右方向键翻页；输入框里不抢键", async () => {
    stubApi();
    const { router } = renderRoutes(appRoutes, "/");
    const search = await screen.findByRole("searchbox", { name: "搜索岗位" });

    fireEvent.keyDown(search, { key: "ArrowRight" });
    expect(router.state.location.pathname).toBe("/");

    fireEvent.keyDown(window, { key: "ArrowRight" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/crawl"));
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("横向拖动能翻页，纵向拖动不翻页", async () => {
    stubApi();
    const { router, container } = renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");
    const viewport = container.querySelector(".mobile-viewport") as HTMLElement;

    fireEvent.pointerDown(viewport, { pointerId: 1, clientX: 200, clientY: 100, button: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 205, clientY: 220 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 205, clientY: 220 });
    expect(router.state.location.pathname).toBe("/");

    fireEvent.pointerDown(viewport, { pointerId: 2, clientX: 220, clientY: 100, button: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 2, clientX: 60, clientY: 104 });
    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 60, clientY: 104 });
    await waitFor(() => expect(router.state.location.pathname).toBe("/crawl"));
  });

  it("滚动超过 36px 顶栏变实，切走再回来仍保留滚动位置", async () => {
    stubApi();
    const { router, container } = renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");
    const pages = container.querySelectorAll<HTMLElement>("section.mobile-page");
    const topbar = container.querySelector(".mobile-topbar") as HTMLElement;

    pages[0].scrollTop = 120;
    fireEvent.scroll(pages[0]);
    expect(topbar.className).toContain("solid");

    fireEvent.click(within(dock()).getByRole("button", { name: "抓取" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/crawl"));
    fireEvent.click(within(dock()).getByRole("button", { name: "岗位库" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(pages[0].scrollTop).toBe(120);
    expect(topbar.className).toContain("solid");
  });

  it("再次点当前 Dock 标签会把页面滚回顶部", async () => {
    stubApi();
    renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");
    const calls = vi.mocked(Element.prototype.scrollTo);
    calls.mockClear();
    fireEvent.click(within(dock()).getByRole("button", { name: "岗位库" }));
    expect(calls).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
  });

  it("页面里触发的 Toast 由外壳渲染", async () => {
    stubApi();
    renderRoutes(appRoutes, "/");
    fireEvent.click(await screen.findByRole("button", { name: "记录投递：大模型应用工程师" }));
    expect(await screen.findByRole("status")).toHaveProperty("textContent", "已记录投递。");
  });

  it("助手抽屉打开时背景 inert，Esc 关闭", async () => {
    stubApi();
    const { router, container } = renderRoutes(appRoutes, "/assistant");
    await waitFor(() => expect(router.state.location.pathname).toBe("/assistant"));
    fireEvent.click(screen.getByRole("button", { name: "会话列表" }));

    const drawer = await screen.findByRole("dialog", { name: "会话列表" });
    expect(within(drawer).getByText("旧会话")).toBeTruthy();
    expect((container.querySelector(".mobile-scene") as HTMLElement).inert).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "会话列表" })).toBeNull());
    expect((container.querySelector(".mobile-scene") as HTMLElement).inert).toBe(false);
  });

  it("主题按钮切换深色 / 浅色", async () => {
    stubApi();
    renderRoutes(appRoutes, "/");
    await screen.findByText("AI产品经理");
    fireEvent.click(screen.getByRole("button", { name: "切换到浅色模式" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("hunter1-theme")).toBe("light");
    expect(screen.getByRole("button", { name: "切换到深色模式" })).toBeTruthy();
  });

  it("未知路径给出回岗位库的出口", async () => {
    stubApi();
    const { router } = renderRoutes(appRoutes, "/nope");
    expect(await screen.findByText("页面不存在")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "回到岗位库" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("抓取进行中时 Dock 显示徽标", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/crawl/status"
        ? jsonResponse({
            running: true,
            started_at: null,
            finished_at: null,
            error: null,
            sites: [],
            total_fetched: 0,
            total_created: 0,
          })
        : undefined,
    );
    const { container } = renderRoutes(appRoutes, "/");
    await waitFor(() => expect(container.querySelector(".mobile-dock-badge.show")).toBeTruthy());
  });
});
