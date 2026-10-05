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
        className="w-80 rounded border border-slate-300 bg-white px-3 py-2"
        placeholder="按岗位名搜索，如：产品经理"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <button
        type="submit"
        className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700"
      >
        搜索
      </button>
    </form>
  );
}
