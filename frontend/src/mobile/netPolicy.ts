/**
 * 明文 HTTP 的边界。
 *
 * 安卓 App 需要在同一 Wi-Fi 里用 `http://192.168.x.x:8000` 连电脑，所以不能
 * 一刀切禁用明文；但把**公网**明文地址放行，等于把访问码 / API Key 交给整条
 * 链路上的任何观察者。因此只在「本机 / 私有网段 / 内网单标签名」上允许 http，
 * 公网一律要求 https。
 */

function isPrivateIpv4(hostname: string): boolean {
  const parts = hostname.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) {
    return false;
  }
  const octets = parts.map(Number) as [number, number, number, number];
  if (octets.some((octet) => octet > 255)) {
    return false;
  }
  const [a, b] = octets;
  if (a === 10 || a === 127) {
    return true;
  }
  if (a === 172 && b >= 16 && b <= 31) {
    return true;
  }
  if (a === 192 && b === 168) {
    return true;
  }
  return a === 169 && b === 254;
}

export function isPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  if (!host) {
    return false;
  }
  if (host === "localhost" || host.endsWith(".localhost")) {
    return true;
  }
  if (host.endsWith(".local") || host.endsWith(".internal")) {
    return true;
  }
  if (host.startsWith("[") || host.includes(":")) {
    // IPv6：只放行回环、未指定、唯一本地（fc00::/7）与链路本地（fe80::/10）。
    // 不能把 `::` 当成「所有以双冒号开头的地址」：`::ffff:8.8.8.8` 是公网
    // IPv4 的映射形式，放行它等于给公网明文开了后门。
    const address = host.replace(/^\[/, "").replace(/\]$/, "").split("%")[0]?.toLowerCase() ?? "";
    if (address === "::1" || address === "::") {
      return true;
    }
    const first = address.split(":")[0] ?? "";
    return /^f[cd][0-9a-f]{2}$/.test(first) || /^fe[89ab][0-9a-f]$/.test(first);
  }
  if (!host.includes(".")) {
    // 单标签名（ollama、gateway、llm-host）：内网主机名，不是公网域名。
    return true;
  }
  return isPrivateIpv4(host);
}

export function isPublicCleartext(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    return url.protocol === "http:" && !isPrivateHost(url.hostname);
  } catch {
    return false;
  }
}

export function assertCleartextAllowed(baseUrl: string): void {
  if (isPublicCleartext(baseUrl)) {
    throw new Error("公网地址不能用明文 http：访问码与 API Key 会以明文过网，请改用 https:// 或内网地址。");
  }
}

/** 两个地址是否属于同一信任边界（scheme + host + port）。 */
export function sameOrigin(left: string, right: string): boolean {
  try {
    return new URL(left).origin === new URL(right).origin;
  } catch {
    return false;
  }
}

/** 校验并归一化 http(s) 端点；与后端 `LLMSettings` 的 base_url 规则对齐。 */
export function normalizeHttpBaseUrl(raw: string): string {
  const text = raw.trim();
  if (!text) {
    throw new Error("base_url 不能为空");
  }
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    throw new Error("base_url 必须是合法的 http(s) 地址");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("base_url 必须以 http:// 或 https:// 开头");
  }
  if (!url.hostname) {
    throw new Error("base_url 缺少主机名");
  }
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.origin}${path}${url.search}`;
}
