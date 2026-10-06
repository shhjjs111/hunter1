import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

/**
 * 前端 lint 配置（ESLint flat config）。
 *
 * 为什么此前没有 lint：主工程用的是 TypeScript 7，而 `typescript-eslint` 的 peer
 * 范围是 `>=4.8.4 <6.1.0`（TS7 尚未提供 JS API，官方 issue #12518 关闭为
 * not planned，要等 7.1）。装不上，于是整个前端没有 lint 这一环。
 *
 * 解法是**降级到 TS 6.0.3**，而不是为 lint 另建一棵依赖树 —— 后者会让 lint 与
 * typecheck 跑在两个 TS 版本上，新造一个「lint 判断与 typecheck 不一致」的漂移面。
 * （`tools/contract-codegen` 的独立树是另一回事：那是独立生成器，输入 json 输出
 * 文本，不需要理解主工程。）
 */
export default tseslint.config(
  {
    ignores: [
      "dist/**",
      // 生成物，禁手改（见 AGENTS.md 契约纪律）
      "src/shared/api/schema.d.ts",
      // 契约生成器的独立依赖树，不参与主工程 lint
      "tools/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    ...reactHooks.configs.flat.recommended,
  },
);
