import { describe, expect, it } from "vitest";

import { apiErrorMessage } from "./errors";

describe("apiErrorMessage", () => {
  it("优先用后端给的 detail（那才是可读原因）", () => {
    expect(apiErrorMessage({ detail: "岗位不存在：abc" }, "加载岗位失败")).toBe(
      "岗位不存在：abc",
    );
  });

  it("5xx 时忽略 detail —— 「Internal Server Error」对用户没有信息量", () => {
    // FastAPI 的默认 500 体就是 {"detail":"Internal Server Error"}；
    // 把它单独给用户，还不如「什么操作失败了 + 状态码」。
    expect(
      apiErrorMessage({ detail: "Internal Server Error" }, "加载岗位失败", { status: 500 }),
    ).toBe("加载岗位失败（HTTP 500）");
  });

  it("4xx 时优先 detail（那是写给用户看的原因）", () => {
    expect(apiErrorMessage({ detail: "岗位不存在：abc" }, "加载失败", { status: 404 })).toBe(
      "岗位不存在：abc",
    );
    expect(apiErrorMessage({ detail: "画像至少要有一项信号" }, "保存失败", { status: 422 })).toBe(
      "画像至少要有一项信号",
    );
  });

  it("detail 为空串/非字符串时不硬用", () => {
    expect(apiErrorMessage({ detail: "" }, "加载岗位失败", { status: 400 })).toBe(
      "加载岗位失败（HTTP 400）",
    );
    expect(apiErrorMessage({ detail: { nested: 1 } }, "加载岗位失败")).toBe("加载岗位失败");
  });

  it("没有 detail 但知道状态码时带上状态码（便于排查）", () => {
    expect(apiErrorMessage(undefined, "读取失败", { status: 503 })).toBe("读取失败（HTTP 503）");
  });

  it("全都没有时给 fallback —— 且绝不把原始 JSON 丢给用户", () => {
    const message = apiErrorMessage({ unexpected: "shape" }, "读取失败");
    expect(message).toBe("读取失败");
    expect(message).not.toContain("{");
  });

  it("error 为 null 也能安全处理", () => {
    expect(apiErrorMessage(null, "读取失败")).toBe("读取失败");
  });
});
