import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyInitialTheme } from "../../app/theme";
import { jsonResponse, renderWithChrome, stubApi, stubMedia } from "../../test-utils";
import { MobileSettingsPage } from "./MobileSettingsPage";

beforeEach(() => {
  stubMedia({ reduced: true });
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

describe("MobileSettingsPage", () => {
  it("回显配置与画像，长 URL 完整呈现在输入框里", async () => {
    stubApi();
    renderWithChrome(<MobileSettingsPage />);
    expect(await screen.findByText("模型已配置")).toBeTruthy();
    expect(screen.getByLabelText("Base URL")).toHaveProperty("value", "https://api.example.com/v1");
    expect(screen.getByLabelText("模型")).toHaveProperty("value", "gpt-test");
    expect(screen.getByLabelText("API Key")).toHaveProperty("placeholder", "sk-…1234");
    expect(await screen.findByLabelText("目标关键词（一行一条）")).toHaveProperty("value", "AI产品经理");
  });

  it("测试连接展示结果", async () => {
    stubApi();
    renderWithChrome(<MobileSettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("● 连接正常 · 312ms")).toBeTruthy();
  });

  it("显示 / 隐藏 API Key", async () => {
    stubApi();
    renderWithChrome(<MobileSettingsPage />);
    const key = await screen.findByLabelText("API Key");
    expect(key).toHaveProperty("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "显示 API Key" }));
    expect(key).toHaveProperty("type", "text");
    fireEvent.click(screen.getByRole("button", { name: "隐藏 API Key" }));
    expect(key).toHaveProperty("type", "password");
  });

  it("保存模型配置并清空 API Key 草稿", async () => {
    stubApi();
    const { chrome } = renderWithChrome(<MobileSettingsPage />);
    const key = await screen.findByLabelText("API Key");
    fireEvent.change(key, { target: { value: "sk-new" } });
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "gpt-next" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "已保存。"));
    expect(key).toHaveProperty("value", "");
  });

  it("保存候选人画像", async () => {
    stubApi();
    const { chrome } = renderWithChrome(<MobileSettingsPage />);
    const keywords = await screen.findByLabelText("目标关键词（一行一条）");
    fireEvent.change(keywords, { target: { value: "AI产品经理\nAgent 产品" } });
    fireEvent.click(screen.getByRole("button", { name: "保存画像" }));
    await waitFor(() => expect(chrome.showToast).toHaveBeenCalledWith("success", "画像已保存。"));
  });

  it("外观分段控件切换主题并记忆选择", async () => {
    stubApi();
    renderWithChrome(<MobileSettingsPage />);
    await screen.findByText("模型已配置");

    fireEvent.click(screen.getByRole("button", { name: "浅色" }));
    expect(window.localStorage.getItem("hunter1-theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "浅色" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "跟随系统" }));
    expect(window.localStorage.getItem("hunter1-theme")).toBe("system");
  });

  it("配置损坏时明确要求重填", async () => {
    stubApi((_request, url) =>
      url.pathname === "/api/settings"
        ? jsonResponse({ base_url: "", model: "", masked_key: "", configured: false, broken: true })
        : undefined,
    );
    renderWithChrome(<MobileSettingsPage />);
    expect(await screen.findByText("已保存的配置不可用 —— 请重新填写并保存")).toBeTruthy();
    expect(screen.getByText(/已保存的模型配置不合法/)).toBeTruthy();
  });
});
