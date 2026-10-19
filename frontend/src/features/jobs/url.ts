/**
 * 只放行 http/https 的详情链接。
 *
 * `detail_url` 来自外部站点的抓取结果，是不可信输入 —— 直接塞进 `href` 等于把
 * 外部内容当链接渲染。取不出 http(s) 时返回 null，调用方退回纯文本。
 */
export function safeHref(raw: string): string | null {
  try {
    const url = new URL(raw, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
