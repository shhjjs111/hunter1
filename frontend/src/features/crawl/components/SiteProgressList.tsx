import { Tag } from "../../../shared/ui";
import type { components } from "../../../shared/api/schema";

type SiteProgress = components["schemas"]["SiteProgressView"];

const STATUS_TONE = {
  pending: "neutral",
  running: "warn",
  ok: "good",
  failed: "bad",
} as const;

const STATUS_LABEL = {
  pending: "等待中",
  running: "抓取中",
  ok: "完成",
  failed: "失败",
} as const;

/** 逐站进度表（纯展示）。 */
export function SiteProgressList({ sites }: { sites: SiteProgress[] }) {
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-left text-slate-500">
          <th className="px-4 py-2 font-medium">站点</th>
          <th className="px-4 py-2 font-medium">状态</th>
          <th className="px-4 py-2 font-medium">抓到</th>
          <th className="px-4 py-2 font-medium">新增</th>
          <th className="px-4 py-2 font-medium">更新</th>
        </tr>
      </thead>
      <tbody>
        {sites.map((site) => (
          <tr key={site.label} className="border-b border-slate-100 last:border-0">
            <td className="px-4 py-2">{site.label}</td>
            <td className="px-4 py-2">
              <Tag tone={STATUS_TONE[site.status as keyof typeof STATUS_TONE] ?? "neutral"}>
                {STATUS_LABEL[site.status as keyof typeof STATUS_LABEL] ?? site.status}
              </Tag>
            </td>
            <td className="px-4 py-2">{site.fetched}</td>
            <td className="px-4 py-2">{site.created}</td>
            <td className="px-4 py-2">{site.updated}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 失败站点单独列出 —— 错误信息不该被埋在表格里。 */
export function FailedSites({ sites }: { sites: SiteProgress[] }) {
  const failed = sites.filter((site) => site.status === "failed");
  if (failed.length === 0) {
    return null;
  }
  return (
    <ul className="mt-4 space-y-1 text-sm text-red-700">
      {failed.map((site) => (
        <li key={site.label}>
          <strong>{site.label}</strong>：{site.error ?? "未知错误"}
        </li>
      ))}
    </ul>
  );
}
