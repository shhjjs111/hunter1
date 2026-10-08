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
      {/* 「上一轮还在跑」只在**确实还在跑**时显示：抓取结束后 status 会变成
          running=false，这条提示必须跟着消失（否则它会永远挂在页面上，用户以为
          自己的点击被永久拒绝了）。 */}
      {start.data && !start.data.started && running && (
        <p className="mb-4 text-sm text-warning">上一轮还在跑，未启动新的。</p>
      )}
      {start.isError && <ErrorNotice message={(start.error as Error).message} />}

      {snapshot === undefined ? (
        // 首屏/读取失败时别渲染一张空白 Card —— 那与「跑过但一个站点都没有」
        // 看起来一样。
        <Card>
          <p className="px-4 py-6 text-sm text-muted">正在读取抓取进度…</p>
        </Card>
      ) : snapshot.sites.length === 0 ? (
        <EmptyState>
          还没有跑过抓取。点右上角「开始抓取」跑一轮；站点来自后端注册表。
        </EmptyState>
      ) : (
        <Card>
          <SiteProgressList sites={snapshot.sites} />
        </Card>
      )}

      {snapshot && <FailedSites sites={snapshot.sites} />}

      <p className="mt-4 text-xs text-muted">
        只抓公开列表页，不做登录、不绕验证码。站点返回风控页时会显式报错，
        不会伪装成「今天没岗位」。
      </p>
    </>
  );
}
