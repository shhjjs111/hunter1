import { describe, expect, it } from "vitest";

import { apiErrorMessage, CLIENT_AUTHORED_ERROR_HEADER } from "./errors";

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

  it("5xx 但带自造标记时用 detail —— 别与漏斗造的错误互相抵消", () => {
    // client.ts 的错误漏斗给「2xx 但不是 JSON」造的是 {detail: "后端返回了非 JSON…"}
    // + 502，并带上标记头（那条 detail 是**我们**写给用户的，不是服务端内部消息）。
    // 一刀切按状态码丢掉它，漏斗写好的中文说明就会在**所有**调用点失效。
    const headers = new Headers({ [CLIENT_AUTHORED_ERROR_HEADER]: "1" });
    expect(
      apiErrorMessage(
        { detail: "后端返回了非 JSON 响应（HTTP 200，Content-Type: text/html）。" },
        "加载配置失败",
        { status: 502, headers },
      ),
    ).toContain("非 JSON");
  });

  it("4xx 时优先 detail（那是写给用户看的原因）", () => {
    expect(apiErrorMessage({ detail: "岗位不存在：abc" }, "加载失败", { status: 404 })).toBe(
      "岗位不存在：abc",
    );
    expect(apiErrorMessage({ detail: "画像至少要有一项信号" }, "保存失败", { status: 422 })).toBe(
      "画像至少要有一项信号",
    );
  });

  it("detail 为 FastAPI 校验错误数组时取出 msg（PUT /settings 的 422 就是这形状）", () => {
    // 后端手工抛 422 时按 FastAPI 同款构造：[{loc, msg, type}]。
    // 只认字符串 detail 的话，用户看到的是「保存失败（HTTP 422）」，字段信息全丢。
    expect(
      apiErrorMessage(
        { detail: [{ loc: ["body", "base_url"], msg: "base_url 必须是 http(s) 地址", type: "value_error" }] },
        "保存失败",
        { status: 422 },
      ),
    ).toBe("base_url 必须是 http(s) 地址");
    // 多条错误合成一句（用「；」分隔，不丢其中任何一条）
    expect(
      apiErrorMessage(
        { detail: [{ msg: "模型名不合法" }, { msg: "密钥不能为空" }] },
        "保存失败",
        { status: 422 },
      ),
    ).toBe("模型名不合法；密钥不能为空");
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
