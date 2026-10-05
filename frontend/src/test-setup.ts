/**
 * 测试环境 setup（vitest 在 jsdom 里加载）。
 *
 * `globals: false` 时 @testing-library/react 不会自动注册 cleanup ——
 * 没有它，每个 render 的 DOM 都留在 document 里，后续用例的查询会
 * 撞到多个匹配元素（"Found multiple elements"）。这里显式挂上。
 */
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
