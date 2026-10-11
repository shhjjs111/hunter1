import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./MessageBubble";

/**
 * `MessageBubble` 此前**零专属测试**（只在 AssistantPage 的集成用例里被间接渲染）。
 * 它是对话区唯一的展示组件，四条行为各钉一颗钉子。
 */
describe("MessageBubble", () => {
  it("用户消息靠右、助手消息靠左", () => {
    const { container: user, unmount: unmountUser } = render(
      <MessageBubble item={{ role: "user", content: "你好" }} />,
    );
    expect(user.firstElementChild?.className).toContain("justify-end");
    unmountUser();

    const { container: assistant } = render(
      <MessageBubble item={{ role: "assistant", content: "在" }} />,
    );
    expect(assistant.firstElementChild?.className).toContain("justify-start");
  });

  it("三种已知角色各有中文标签", () => {
    for (const [role, label] of [
      ["user", "你"],
      ["assistant", "助手"],
      ["tool", "工具"],
    ] as const) {
      const { unmount } = render(<MessageBubble item={{ role, content: "x" }} />);
      expect(screen.getByText(label)).toBeDefined();
      unmount();
    }
  });

  it("未知角色回落到角色名本身，标签不空着", () => {
    // 实现是 `ROLE_LABEL[role] ?? role` —— 后端将来新增角色时，标签栏不能空着
    // （空标签让这条消息看起来没有归属）。
    const { unmount } = render(<MessageBubble item={{ role: "system", content: "x" }} />);
    expect(screen.getByText("系统")).toBeDefined();
    unmount();

    render(<MessageBubble item={{ role: "weird", content: "y" }} />);
    expect(screen.getByText("weird")).toBeDefined();
  });

  it("内容保留换行（whitespace-pre-wrap）并能断长词（break-words）", () => {
    render(<MessageBubble item={{ role: "assistant", content: "第一行\n第二行" }} />);
    const body = screen.getByText(/第一行/);
    expect(body.className).toContain("whitespace-pre-wrap");
    // 长 URL / 无空格串不折行会撑破气泡
    expect(body.className).toContain("break-words");
  });
});
