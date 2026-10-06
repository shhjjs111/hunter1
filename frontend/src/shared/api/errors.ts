/**
 * 把 API 错误转成**给用户看的一句话**。
 *
 * 后端在 `detail` 里给的是可读原因（「岗位不存在」「画像至少要有一项信号」
 * 「模型未配置：请先在「配置」页…」）。直接 `JSON.stringify(error)` 会把这层
 * 意思埋进 JSON 里丢给用户，看到的是：
 *
 *     加载岗位失败：{"detail":"..."}
 *
 * 抽成共享函数而不是各处手写 —— 之前就有 6 处各写各的（有的取 `detail`、
 * 有的 stringify），最后必然分叉成不同措辞。
 *
 * ## 为什么按状态码分两种取法
 *
 * **4xx 优先用 `detail`**：这类是「请求有问题」，后端的 detail 是写给用户看的
 * 具体原因（岗位不存在 / 画像为空 / 模型没配）。
 *
 * **5xx 一律用 `fallback（HTTP n）`**：服务端自己坏了，它的 detail 通常是
 * FastAPI 默认的 `"Internal Server Error"` —— 把这句话单独给用户，信息量
 * 还不如「加载岗位失败（HTTP 500）」。保留「什么操作失败了」+ 状态码更好排查。
 */
export function apiErrorMessage(
  error: unknown,
  fallback: string,
  response?: { status: number },
): string {
  const status = response?.status;
  const isServerError = status != null && status >= 500;
  const detail = (error as { detail?: unknown } | null | undefined)?.detail;

  if (!isServerError && typeof detail === "string" && detail.trim() !== "") {
    return detail;
  }
  if (status != null) {
    return `${fallback}（HTTP ${status}）`;
  }
  return fallback;
}
