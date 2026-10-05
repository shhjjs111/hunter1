import { Button, Card, EmptyState, ErrorNotice, PageHeader } from "../../shared/ui";
import { useCrawlStatus, useStartCrawl } from "./api";
import { FailedSites, SiteProgressList } from "./components/SiteProgressList";

export function CrawlPage() {
  const status = useCrawlStatus();
  const start = useStartCrawl();
  const snapshot = status.data;
  const running = snapshot?.running ?? false;

  return (
    <>
      <PageHeader
        title="抓取"
        subtitle={
          snapshot ? (
            <>
              本轮共抓到 {snapshot.total_fetched} 条
              {snapshot.error ? ` · ${snapshot.error}` : ""}
            </>
          ) : (
            "正在读取状态…"
          )
        }
        actions={
          <Button
            variant="primary"
            disabled={running || start.isPending}
            onClick={() => start.mutate()}
          >
            {running ? "正在抓取…" : "开始抓取"}
          </Button>
        }
      />

      {status.isError && <ErrorNotice message={(status.error as Error).message} />}
      {start.data && !start.data.started && (
        <p className="mb-4 text-sm text-amber-700">上一轮还在跑，未启动新的。</p>
      )}
      {start.isError && <ErrorNotice message={(start.error as Error).message} />}

      {snapshot && snapshot.sites.length === 0 ? (
        <EmptyState>
          还没有跑过抓取。点右上角「开始抓取」跑一轮；站点来自后端注册表。
        </EmptyState>
      ) : (
        <Card>
          {snapshot && <SiteProgressList sites={snapshot.sites} />}
        </Card>
      )}

      {snapshot && <FailedSites sites={snapshot.sites} />}

      <p className="mt-4 text-xs text-slate-500">
        只抓公开列表页，不做登录、不绕验证码。站点返回风控页时会显式报错，
        不会伪装成「今天没岗位」。
      </p>
    </>
  );
}
