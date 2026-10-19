import type { components } from "../../../shared/api/schema";

type SiteProgress = components["schemas"]["SiteProgressView"];

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "抓取中",
  ok: "完成",
  failed: "失败",
};

const STATUS_TONE: Record<string, string> = {
  pending: "neutral",
  running: "warn",
  ok: "good",
  failed: "bad",
};

export function SiteCard({ site }: { site: SiteProgress }) {
  const tone = STATUS_TONE[site.status] ?? "neutral";
  const label = STATUS_LABEL[site.status] ?? site.status;
  return (
    <div className="mobile-card mobile-site-card">
      <div>
        <div className="mobile-site-name">{site.label}</div>
        <div className="mobile-site-detail">
          {site.status === "pending"
            ? "—"
            : `抓到 ${site.fetched} · 新增 ${site.created} · 更新 ${site.updated}`}
        </div>
        {site.status === "failed" && site.error && (
          <div className="mobile-site-error">{site.error}</div>
        )}
      </div>
      <span className={`mobile-tag ${tone}`}>
        {site.status === "running" && <span className="mobile-pulse" aria-hidden="true" />}
        {label}
      </span>
    </div>
  );
}
