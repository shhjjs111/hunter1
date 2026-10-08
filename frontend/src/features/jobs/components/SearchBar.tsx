import { useState } from "react";

import { fieldClass } from "../../../shared/ui";

/**
 * 搜索框（受控草稿 + 提交回调）—— 输入过程不触发请求，回车/点搜索才提交。
 *
 * 这个输入框没有可见的 `<label>`（版面上就是「输入框 + 搜索按钮」），所以给
 * `aria-label`：只靠 `placeholder` 时，屏幕阅读器读不出它是干什么的
 * （placeholder 不是可访问名，且输入后即消失）。
 */
export function SearchBar({
  initial,
  onSearch,
}: {
  initial: string;
  onSearch: (keyword: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <form
      className="flex gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSearch(value.trim());
      }}
    >
      <input
        // 窄屏自适应：固定 w-80（320px）在 390px 宽的窗口里会连同按钮一起溢出。
        // 用 flex-1 + max-w 让它随容器收缩，按钮 shrink-0 保证不被挤没。
        className={`min-w-0 flex-1 sm:max-w-80 ${fieldClass}`}
        placeholder="按岗位名搜索，如：产品经理"
        aria-label="按岗位名搜索"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <button
        type="submit"
        className="shrink-0 rounded bg-ink px-4 py-2 text-white hover:bg-ink-soft"
      >
        搜索
      </button>
    </form>
  );
}
