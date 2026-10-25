import { useMobileChrome } from "../../app/mobileChrome";
import { useCrawlStatus, useStartCrawl } from "./api";
import { SiteCard } from "./components/SiteCard";

export function MobileCrawlPage() {
  const chrome = useMobileChrome();
  const status = useCrawlStatus();
  const start = useStartCrawl();
  const snapshot = status.data;
  const running = snapshot?.running ?? false;
  const sites = snapshot?.sites ?? [];
  const fetched = sites.reduce((sum, site) => sum + site.fetched, 0);
  const created = sites.reduce((sum, site) => sum + site.created, 0);
  const updated = sites.reduce((sum, site) => sum + site.updated, 0);
  const failed = sites.filter((site) => site.status === "failed").length;

  const subtitle = status.isError
    ? "读取失败"
    : snapshot?.error
      ? "本轮抓取失败"
    : snapshot
      ? sites.length === 0 && !running
        ? "本次启动后还没有抓取"
        : `本轮共抓到 ${fetched} 条${failed > 0 ? ` · ${failed} 个站点失败` : ""}`
      : "正在读取状态…";

  return (
    <div>
      <h1 className="mobile-title">抓取</h1>
      <p className="mobile-subtitle num">{subtitle}</p>

      <div className="mobile-stats">
        <div className="mobile-card mobile-stat">
          <b>{fetched}</b>
          <span>抓到</span>
        </div>
        <div className="mobile-card mobile-stat">
          <b>{created}</b>
          <span>新增</span>
        </div>
        <div className="mobile-card mobile-stat">
          <b>{updated}</b>
          <span>更新</span>
        </div>
      </div>

      <button
        type="button"
        className="mobile-button primary block"
        disabled={running || start.isPending}
        onClick={() =>
          start.mutate(undefined, {
            onSuccess: (result) => {
              if (result.started) {
                chrome.showToast("success", "已开始抓取。");
              }
            },
            onError: (error) => chrome.showToast("error", error.message),
          })
        }
      >
        {running || start.isPending ? (
          <>
            <span className="mobile-spinner" aria-hidden="true" />
            正在抓取…
          </>
        ) : sites.length > 0 ? (
          "再抓一轮"
        ) : (
          "开始抓取"
        )}
      </button>

      {start.data && !start.data.started && running && (
        <p className="mt-3 text-sm text-warning">上一轮还在跑，未启动新的。</p>
      )}
      {start.isError && <p className="mobile-error mt-3">{(start.error as Error).message}</p>}
      {status.isError && <p className="mobile-error mt-3">{(status.error as Error).message}</p>}
      {snapshot?.error && <p className="mobile-error mt-3">{snapshot.error}</p>}

      <div className="mobile-site-list mt-4">
        {status.isLoading && !snapshot && (
          <div className="mobile-empty">正在读取抓取进度…</div>
        )}
        {!status.isLoading && !status.isError && snapshot && !snapshot.error && !running && sites.length === 0 && (
          <div className="mobile-empty">
            本次启动后还没有抓取。打开 App 时会按 24 小时窗口自动补抓，也可以点上方
            「开始抓取」。
          </div>
        )}
        {sites.map((site) => (
          <SiteCard key={site.label} site={site} />
        ))}
      </div>

      <p className="mobile-foot">
        打开 App 时每天自动补抓一轮；已有 JD 的岗位不会重复抓详情。<br />
        只抓公开列表页，不做登录、不绕验证码。<br />
        站点返回风控页时会显式报错，不会伪装成「今天没岗位」。
      </p>
    </div>
  );
}
