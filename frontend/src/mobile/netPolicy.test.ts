import { describe, expect, it } from "vitest";

import {
  assertCleartextAllowed,
  isPrivateHost,
  isPublicCleartext,
  normalizeHttpBaseUrl,
  sameOrigin,
} from "./netPolicy";

describe("明文 HTTP 边界", () => {
  it.each([
    "localhost",
    "127.0.0.1",
    "10.1.2.3",
    "172.16.0.9",
    "172.31.255.254",
    "192.168.1.5",
    "169.254.10.1",
    "nas.local",
    "ollama",
    "[::1]",
    "[fd00::1]",
  ])("内网主机允许 http：%s", (host) => {
    expect(isPrivateHost(host)).toBe(true);
  });

  it.each([
    "8.8.8.8",
    "999.1.1.1",
    "api.example.com",
    "172.32.0.1",
    "192.169.1.1",
    "example.dev",
    "[2001:4860:4860::8888]",
    "[::ffff:8.8.8.8]",
  ])(
    "公网主机不允许 http：%s",
    (host) => {
      expect(isPrivateHost(host)).toBe(false);
    },
  );

  it("assertCleartextAllowed 只拦公网 http", () => {
    expect(() => assertCleartextAllowed("http://192.168.1.5:8000")).not.toThrow();
    expect(() => assertCleartextAllowed("http://example.com/v1")).toThrow(/公网地址/);
    expect(() => assertCleartextAllowed("https://example.com/v1")).not.toThrow();
    expect(isPublicCleartext("http://example.com/v1")).toBe(true);
    expect(isPublicCleartext("https://example.com/v1")).toBe(false);
  });

  it("sameOrigin 比较 scheme、主机与端口", () => {
    expect(sameOrigin("https://api.example.com/v1", "https://api.example.com/v2")).toBe(true);
    expect(sameOrigin("https://api.example.com", "https://api.example.com:443")).toBe(true);
    expect(sameOrigin("http://api.example.com", "https://api.example.com")).toBe(false);
    expect(sameOrigin("https://api.example.com", "https://evil.example.com")).toBe(false);
    expect(sameOrigin("https://api.example.com:8443", "https://api.example.com")).toBe(false);
  });

  it("normalizeHttpBaseUrl 校验协议与主机名并去掉尾部斜杠", () => {
    expect(normalizeHttpBaseUrl(" https://API.example.com/v1/ ")).toBe(
      "https://api.example.com/v1",
    );
    expect(normalizeHttpBaseUrl("http://127.0.0.1:11434/")).toBe("http://127.0.0.1:11434");
    expect(() => normalizeHttpBaseUrl("not-a-url")).toThrow(/合法的 http/);
    expect(() => normalizeHttpBaseUrl("ftp://example.com/v1")).toThrow(/http:\/\//);
  });
});
