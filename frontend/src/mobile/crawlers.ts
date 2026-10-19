import { requestText, readableHttpError } from "./http";
import type { CaptureStatus } from "./db";

export interface SiteSpec {
  key: string;
  label: string;
  urlTemplate: string;
  itemSelector: string;
  titleSelector: string;
  linkSelector?: string;
  citySelector?: string;
  companySelector?: string;
  maxPages: number;
  detailSelector?: string;
  detailReferer?: string;
}

export interface RawJob {
  source: string;
  company: string;
  title: string;
  city: string | null;
  detailUrl: string;
  jdRaw: string | null;
  captureStatus: CaptureStatus;
}

export const SITES: SiteSpec[] = [
  {
    key: "shixiseng",
    label: "实习僧",
    urlTemplate: "https://www.shixiseng.com/interns?page={page}",
    itemSelector: ".intern-wrap",
    titleSelector: "a.title.font",
    linkSelector: "a.title.font",
    citySelector: "span.city",
    companySelector: ".f-r .title",
    // 实测 `?page=N` 可翻页（列表页底部是 .el-pagination，最多 500 页）。
    // 先抓 5 页 ≈ 100 条/天；观察站点风控后再往上调。
    maxPages: 5,
    detailSelector: ".con-job",
  },
  {
    key: "liepin_campus",
    label: "猎聘校园",
    urlTemplate: "https://www.liepin.com/campus/",
    itemSelector: ".campus-recommend-jobs-item",
    titleSelector: ".jobcard2-title",
    linkSelector: "a.jobcard2-detail-box",
    citySelector: ".jobcard2-detail-des span",
    companySelector: ".jobcard2-company-title",
    // 落地页没有公开翻页入口（页面由 JS 填充推荐流），只能抓第一页。
    maxPages: 1,
    detailSelector: ".job-intro-container",
  },
  {
    key: "gaoxiaojob",
    label: "高校人才网",
    urlTemplate: "https://www.gaoxiaojob.com/",
    itemSelector: ".job-board .list ul li",
    titleSelector: ".position h6",
    linkSelector: "a",
    citySelector: ".region .city",
    companySelector: ".region .school",
    // 落地页是静态推荐位；`/job` 列表页对匿名请求返回 403 且正文由 JS 渲染。
    maxPages: 1,
    detailSelector: ".detail-item",
    detailReferer: "https://www.gaoxiaojob.com/",
  },
  {
    key: "job910",
    label: "万行教师人才网",
    urlTemplate: "https://www.job910.com/job/?page={page}",
    itemSelector: ".job-item",
    titleSelector: "a.left-name",
    linkSelector: "a.left-name",
    citySelector: ".content-left-2-left li:nth-child(2)",
    companySelector: ".content-left-1-right a",
    // ⚠️ `?page=N` 只是回显同一页：列表 HTML 固定 10 条，翻页由登录态 JS API
    // （searchapi.job910.com，带 Bearer）驱动。匿名抓取只能要第一页。
    maxPages: 1,
    detailSelector: ".jobs-desc",
  },
  {
    key: "yingjiesheng",
    label: "应届生",
    urlTemplate: "https://www.yingjiesheng.com/",
    itemSelector: ".enterprise-list-item",
    titleSelector: ".enterprise-list-title",
    // 首页只有项目推荐流，没有公开翻页入口。
    maxPages: 1,
  },
];

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 Hunter1/0.4";
export const DETAIL_LIMIT = 60;
const MAX_JD_CHARS = 200_000;

/**
 * 请求间隔。移动端每天只跑一轮，间隔比桌面后端略短，但不能没有：
 * 连续快速打列表页/详情页是最容易被风控识别的形态。
 *
 * 在测试里归零，避免用例为了等真实间隔而超时；生产构建始终使用下面的值。
 */
const TEST_MODE = import.meta.env.MODE === "test";
const PAGE_DELAY_MS = 900;
const DETAIL_DELAY_MS = 700;
const DELAY_JITTER_MS = 350;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 固定间隔容易被识别成脚本，加一点随机抖动更像人工浏览。 */
export function pauseMs(baseMs: number, random = Math.random): number {
  return baseMs + Math.floor(random() * DELAY_JITTER_MS);
}

async function pacedDelay(baseMs: number): Promise<void> {
  if (TEST_MODE) {
    return;
  }
  await delay(pauseMs(baseMs));
}

function firstText(node: Element | null): string | null {
  if (!node) {
    return null;
  }
  for (const line of (node.textContent ?? "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  const candidates = [node, ...Array.from(node.querySelectorAll("*"))];
  for (const candidate of candidates) {
    for (const attr of ["title", "alt"] as const) {
      const value = candidate.getAttribute(attr);
      if (value?.trim()) {
        return value.trim();
      }
    }
  }
  return null;
}

function selectOne(item: Element, selector: string | undefined): Element | null {
  return selector ? item.querySelector(selector) : null;
}

function itemHref(item: Element, spec: SiteSpec): string {
  let node: Element | null = null;
  if (spec.linkSelector) {
    node = item.querySelector(spec.linkSelector);
  } else if (item instanceof HTMLAnchorElement && item.getAttribute("href")?.trim()) {
    node = item;
  }
  node ??= item.querySelector("a");
  return node?.getAttribute("href")?.trim() ?? "";
}

export function parseListPage(html: string, spec: SiteSpec, pageUrl: string): RawJob[] {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const jobs: RawJob[] = [];
  const seen = new Set<string>();

  for (const item of Array.from(doc.querySelectorAll(spec.itemSelector))) {
    const title = firstText(item.querySelector(spec.titleSelector));
    const href = itemHref(item, spec);
    if (!title || !href) {
      continue;
    }
    let detailUrl: string;
    try {
      detailUrl = new URL(href, pageUrl).toString();
    } catch {
      continue;
    }
    if (detailUrl === pageUrl || seen.has(detailUrl)) {
      continue;
    }
    seen.add(detailUrl);
    jobs.push({
      source: spec.label,
      company: firstText(selectOne(item, spec.companySelector)) ?? spec.label,
      title,
      city: firstText(selectOne(item, spec.citySelector)),
      detailUrl,
      jdRaw: null,
      captureStatus: spec.detailSelector ? "pending" : "unknown",
    });
  }
  return jobs;
}

export function parseDetailPage(html: string, selector: string): string | null {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const parts = Array.from(doc.querySelectorAll(selector))
    .map((node) => (node.textContent ?? "").replace(/\s+/g, " ").trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return null;
  }
  const text = parts.join("\n");
  return text.length > MAX_JD_CHARS ? `${text.slice(0, MAX_JD_CHARS)}\n…（岗位描述过长，已截断）` : text;
}

async function fetchHtml(url: string, referer?: string): Promise<string> {
  const headers: Record<string, string> = { "User-Agent": USER_AGENT };
  if (referer) {
    headers.Referer = referer;
  }
  const response = await requestText(url, { headers, timeoutMs: 30_000 });
  if (response.status < 200 || response.status >= 300) {
    throw new Error(httpFailureSummary(response.status, response.text));
  }
  return response.text;
}

/**
 * 把抓取失败的响应变成人能读的一句话。
 *
 * 风控页通常是「403 + 一小段会跳转的 HTML」，原样塞进站点卡片会把整块界面
 * 变成标签汤；其他错误也只保留去标签后的正文摘要。
 */
export function httpFailureSummary(status: number, body: string): string {
  if (status === 403 || status === 429) {
    return `HTTP ${status}：站点拒绝访问（疑似风控），本轮跳过`;
  }
  const text = body
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text ? `HTTP ${status}：${text.slice(0, 120)}` : `HTTP ${status}：站点返回空响应`;
}

export interface SiteProgress {
  key: string;
  label: string;
  status: "pending" | "running" | "ok" | "failed";
  fetched: number;
  created: number;
  updated: number;
  error: string | null;
}

export async function crawlSite(
  spec: SiteSpec,
  onProgress: (progress: Partial<SiteProgress>) => void,
  saveBatch: (jobs: RawJob[]) => Promise<{ created: number; updated: number }>,
  needsDetail: (job: RawJob) => boolean = () => true,
): Promise<SiteProgress> {
  const progress: SiteProgress = {
    key: spec.key,
    label: spec.label,
    status: "running",
    fetched: 0,
    created: 0,
    updated: 0,
    error: null,
  };
  onProgress(progress);

  const seen = new Set<string>();
  let detailFetched = 0;
  let previousHtml: string | null = null;
  try {
    const lastPage = spec.urlTemplate.includes("{page}") ? spec.maxPages : 1;
    for (let page = 1; page <= lastPage; page += 1) {
      const pageUrl = spec.urlTemplate.replace("{page}", String(page));
      const html = await fetchHtml(pageUrl);
      // 有些站点翻页参数失效时会原样返回上一页。继续抓只会重复同一批 URL，
      // 白白增加请求量，所以直接停在「内容没有变化」的边界上。
      if (previousHtml !== null && html === previousHtml) {
        break;
      }
      previousHtml = html;
      const batch = parseListPage(html, spec, pageUrl);
      if (batch.length === 0) {
        break;
      }
      const fresh: RawJob[] = [];
      for (const job of batch) {
        if (seen.has(job.detailUrl)) {
          continue;
        }
        seen.add(job.detailUrl);
        progress.fetched += 1;
        if (spec.detailSelector && needsDetail(job) && detailFetched < DETAIL_LIMIT) {
          detailFetched += 1;
          try {
            job.jdRaw = parseDetailPage(
              await fetchHtml(job.detailUrl, spec.detailReferer),
              spec.detailSelector,
            );
            job.captureStatus = job.jdRaw ? "complete" : "pending";
          } catch {
            job.captureStatus = "failed";
          }
          await pacedDelay(DETAIL_DELAY_MS);
        }
        fresh.push(job);
      }
      if (fresh.length > 0) {
        const outcome = await saveBatch(fresh);
        progress.created += outcome.created;
        progress.updated += outcome.updated;
      }
      onProgress({ ...progress });
      if (page < lastPage) {
        await pacedDelay(PAGE_DELAY_MS);
      }
    }
    progress.status = "ok";
  } catch (error) {
    progress.status = "failed";
    progress.error = readableHttpError(error);
  }
  onProgress(progress);
  return progress;
}
