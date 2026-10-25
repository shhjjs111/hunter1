import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { APPLICATIONS } from "../../../test-utils";
import { ApplicationCard } from "./ApplicationCard";

function renderCard(overrides: Partial<Parameters<typeof ApplicationCard>[0]> = {}) {
  const props = {
    application: APPLICATIONS[0],
    stage: APPLICATIONS[0].stage,
    deleting: false,
    onOpenStage: vi.fn(),
    onSaveNote: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<ApplicationCard {...props} />) };
}

describe("ApplicationCard", () => {
  it("未编辑时备注跟随刷新，未保存的草稿在刷新时保留", () => {
    const { props, rerender } = renderCard();
    const input = screen.getByRole("textbox", { name: "字节跳动的备注" }) as HTMLInputElement;
    rerender(<ApplicationCard {...props} application={{ ...props.application, note: "同步的新备注" }} />);
    expect(input.value).toBe("同步的新备注");
    fireEvent.change(input, { target: { value: "正在编辑" } });
    rerender(<ApplicationCard {...props} application={{ ...props.application, note: "另一个窗口的备注" }} />);
    expect(input.value).toBe("正在编辑");
  });

  it("展示公司与岗位，阶段按钮可打开 Action Sheet", () => {
    const { props } = renderCard();
    expect(screen.getByText("字节跳动")).toBeTruthy();
    expect(screen.getByText("AI产品经理")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "修改字节跳动的投递阶段" }));
    expect(props.onOpenStage).toHaveBeenCalledTimes(1);
  });

  it("面试阶段的进度条走到第三段，已拒阶段整条变灰", () => {
    const { container, rerender } = renderCard({ stage: "interview" });
    expect(container.querySelectorAll(".mobile-steps i.done")).toHaveLength(3);

    rerender(
      <ApplicationCard
        application={APPLICATIONS[1]}
        stage="rejected"
        deleting={false}
        onOpenStage={vi.fn()}
        onSaveNote={vi.fn()}
      />,
    );
    expect(container.querySelector(".mobile-steps")?.className).toContain("off");
    expect(container.querySelectorAll(".mobile-steps i.done")).toHaveLength(0);
  });

  it("备注变化后失焦才保存，回车触发失焦", () => {
    const { props } = renderCard();
    const note = screen.getByRole("textbox", { name: "字节跳动的备注" });
    fireEvent.blur(note);
    expect(props.onSaveNote).not.toHaveBeenCalled();

    fireEvent.change(note, { target: { value: "约了下周一面" } });
    fireEvent.blur(note);
    expect(props.onSaveNote).toHaveBeenCalledWith("约了下周一面");

    fireEvent.change(note, { target: { value: "再改一次" } });
    fireEvent.keyDown(note, { key: "Enter" });
    expect(document.activeElement).not.toBe(note);
  });

  it("删除中时卡片带折叠类名", () => {
    const { container } = renderCard({ deleting: true });
    expect(container.querySelector(".mobile-application-card")?.className).toContain("deleting");
  });
});
