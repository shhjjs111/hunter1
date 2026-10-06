import { useState } from "react";

/**
 * 搜索框（受控草稿 + 提交回调）—— 输入过程不触发请求，回车/点搜索才提交。
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
        className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-3 py-2 sm:max-w-80"
        placeholder="按岗位名搜索，如：产品经理"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <button
        type="submit"
        className="shrink-0 rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700"
      >
        搜索
      </button>
    </form>
  );
}
