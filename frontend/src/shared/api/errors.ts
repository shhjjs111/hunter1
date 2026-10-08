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
 * （网络层的失败不走这条路：`client.ts` 的漏斗把它转成可读错误直接抛出，
 * 页面渲染的就是那条消息。）
 *
 * ## detail 的两种形状都要认
 *
 * 后端按语义给了两种 detail，都是「可读原因」，不该只有前端分不清：
 * - **字符串**：路由手工写的原因（「岗位不存在」「画像至少要有一项信号」）；
 * - **数组**：FastAPI 校验错误 `[{loc, msg, type}]`（含手工抛 422 但按同一形状
 *   构造的那些）。只认字符串时用户看到的是「保存失败（HTTP 422）」，
 *   具体错在哪个字段反而丢了。
 */
export function apiErrorMessage(
  error: unknown,
  fallback: string,
  response?: { status: number },
): string {
  const status = response?.status;
  const isServerError = status != null && status >= 500;
  const detail = (error as { detail?: unknown } | null | undefined)?.detail;

  if (!isServerError) {
    const readable = detailText(detail);
    if (readable !== null) {
      return readable;
    }
  }
  if (status != null) {
    return `${fallback}（HTTP ${status}）`;
  }
  return fallback;
}

/** detail → 一句人能读的话；取不出可读内容时返回 null（调用方退回 fallback）。 */
function detailText(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail.trim() === "" ? null : detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item !== null && typeof item === "object" && typeof (item as { msg?: unknown }).msg === "string"
          ? ((item as { msg: string }).msg.trim())
          : "",
      )
      .filter((message) => message !== "");
    return messages.length > 0 ? messages.join("；") : null;
  }
  return null;
}
