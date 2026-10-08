import { Link } from "react-router";

/**
 * 未匹配路径的兜底页。
 *
 * 没有它时 `path: "*"` 缺席 —— 访问一个不存在的路径（用户手打 URL、旧书签、
 * 打包后未注册的深链）时布局照常渲染（导航还在），内容区**一片空白**：
 * 看起来像「页面坏了」，而不是「这个地址不存在」。
 *
 * 放在 `app/` 而不是某个 feature 下面：它不属于任何业务切片，是外壳的一部分
 * （与 AppLayout / ErrorBoundary 同级）。
 */
export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <h1 className="mb-2 text-2xl font-semibold">页面不存在</h1>
      <p className="mb-6 text-muted-strong">
        这个地址没有对应的页面（可能打错了，或者来自旧版本的链接）。
      </p>
      <Link className="rounded border border-field bg-surface px-3 py-1.5 text-sm" to="/">
        回到岗位库
      </Link>
    </div>
  );
}
