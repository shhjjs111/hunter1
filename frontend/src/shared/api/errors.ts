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
 * ## 为什么按状态码分两种取法（外加一个例外）
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
 * **例外：`client.ts` 自造的响应。**「2xx 但不是 JSON」时漏斗会合成一条
 * `{detail: "后端返回了非 JSON 响应…"}` + 502 —— 那是**我们自己写给用户**的原因，
 * 不是服务端内部消息，带 `CLIENT_AUTHORED_ERROR_HEADER`。据此放行；否则两个模块
 * 各自都对、串起来却互相抵消：漏斗写好的中文说明在**所有**调用点被丢掉，
 * 用户又只剩「加载配置失败（HTTP 502）」。
 *
 * ## detail 的两种形状都要认
 *
 * 后端按语义给了两种 detail，都是「可读原因」，不该只有前端分不清：
 * - **字符串**：路由手工写的原因（「岗位不存在」「画像至少要有一项信号」）；
 * - **数组**：FastAPI 校验错误 `[{loc, msg, type}]`（含手工抛 422 但按同一形状
 *   构造的那些）。只认字符串时用户看到的是「保存失败（HTTP 422）」，
 *   具体错在哪个字段反而丢了。
 */

/**
 * 标记头：`client.ts` 的错误漏斗自造响应时带上它，用来把「**我们**写给用户的
 * 可读原因」与「服务端 5xx 的内部消息」区分开（见文件头的取法说明）。
 */
export const CLIENT_AUTHORED_ERROR_HEADER = "X-Hunter1-Client-Error";

export function apiErrorMessage(
  error: unknown,
  fallback: string,
  response?: { status: number; headers?: { get(name: string): string | null } },
): string {
  const status = response?.status;
  const isServerError = status != null && status >= 500;
  // 例外：漏斗自造的 5xx —— 它的 detail 是我们写给用户的，不该被当成服务端内部消息。
  const clientAuthored = response?.headers?.get(CLIENT_AUTHORED_ERROR_HEADER) != null;
  const detail = (error as { detail?: unknown } | null | undefined)?.detail;

  if (!isServerError || clientAuthored) {
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

/**
 * detail → 一句人能读的话；取不出可读内容时返回 null（调用方退回 fallback）。
 *
 * 导出而不是留私有：SSE 原语（`shared/streaming/sse.ts`）也要从错误体里取出同一层
 * 「可读原因」，它原先自己写了一份只认字符串 detail 的重复实现（数组 detail 直接漏掉）。
 * 让两处共用一个取法，避免措辞/形状规则分叉。
 */
export function detailText(detail: unknown): string | null {
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
