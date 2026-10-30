export type PublishedTemplate = {
  templateKind?: "part" | "component";
  engineeringStatus?: "incomplete" | "ready";
  memberCount?: number;
  publicationId: string;
  templateId: string;
  version: number;
  code: string;
  name: string;
  createdAt: string;
  sha256: string;
  size: number;
  downloadUrl: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parsePublishedTemplates(value: unknown): PublishedTemplate[] {
  const invalid = () => new Error("发布库返回的数据格式不正确，请检查共享发布库。");
  if (!isRecord(value) || !Array.isArray(value.items) || value.count !== value.items.length) throw invalid();
  const ids = new Set<string>();
  const items = value.items.map((item: unknown) => {
    if (!isRecord(item) ||
      !["publicationId", "templateId", "name", "code", "createdAt", "downloadUrl"].every(
        key => typeof item[key] === "string" && (item[key] as string).trim().length > 0,
      ) ||
      typeof item.version !== "number" || !Number.isSafeInteger(item.version) || item.version < 1 ||
      typeof item.size !== "number" || !Number.isSafeInteger(item.size) || item.size < 0 ||
      typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(item.sha256) ||
      (item.templateKind !== undefined && item.templateKind !== "part" && item.templateKind !== "component") ||
      !Number.isFinite(Date.parse(item.createdAt as string)) || ids.has(item.publicationId as string)
    ) throw invalid();
    ids.add(item.publicationId as string);
    return item as PublishedTemplate;
  });
  return items.sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

const errorMessages: Record<string, string> = {
  REMOTE_STORAGE_INTEGRITY_ERROR: "\u6a21\u677f\u5305\u6821\u9a8c\u5931\u8d25\uff0c\u8bf7\u8054\u7cfb\u53d1\u5e03\u8005\u786e\u8ba4\u53d1\u5e03\u7248\u672c\u3002",
  COMPONENT_LIBRARY_NOT_CONFIGURED: "尚未配置发布库连接密钥，请按接入说明完成本机配置后重启组件平台。",
  COMPONENT_LIBRARY_INVALID_URL: "发布库服务地址配置不正确，请检查本机配置后重启组件平台。",
  COMPONENT_LIBRARY_INVALID_KEY: "发布库连接密钥格式不正确，请使用不含换行的英文字符密钥并重启组件后端。",
  PUBLIC_API_NOT_CONFIGURED: "共享发布库读取未启用，请检查 MinIO 配置后重启组件平台。",
  PUBLIC_API_KEY_INVALID: "发布库连接密钥不正确，请确认两个平台配置的密钥一致。",
  REMOTE_STORAGE_NOT_CONFIGURED: "尚未配置共享发布库，请检查 MinIO 配置后重启组件平台。",
  REMOTE_STORAGE_UNAVAILABLE: "共享发布库暂时无法访问，请稍后重试或检查服务连接。",
  LIBRARY_UNREACHABLE: "共享发布库暂时无法访问，请检查 MinIO 服务或网络连接后重试。",
  LIBRARY_TIMEOUT: "读取发布库超时，请稍后重试。",
  LIBRARY_INVALID_RESPONSE: "发布库返回的数据格式不正确，请检查共享发布库。",
  LIBRARY_UPSTREAM_ERROR: "发布库服务暂时不可用，请稍后重试。",
};

export function formatLibraryError(error: string, hasCachedItems: boolean): string {
  return hasCachedItems ? `当前显示已缓存模板，暂时无法检查更新。${error}` : error;
}

export async function fetchPublishedTemplates(signal: AbortSignal): Promise<PublishedTemplate[]> {
  return parsePublishedTemplates(await libraryResponse("/api/published-templates", signal));
}

async function libraryResponse(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, {
    signal, cache: "no-store", headers: { Accept: "application/json" },
  });
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error(errorMessages.LIBRARY_INVALID_RESPONSE); }
  if (!response.ok) {
    const code = isRecord(body) && isRecord(body.error) && typeof body.error.code === "string" ? body.error.code : "";
    throw new Error(errorMessages[code] || errorMessages.LIBRARY_UPSTREAM_ERROR);
  }
  return body;
}

export async function fetchAvailableTemplates(signal: AbortSignal, onUpdate?: (items: PublishedTemplate[]) => void): Promise<PublishedTemplate[]> {
  const items = (await fetchPublishedTemplates(signal)).filter(item => item.templateKind !== "component");
  signal.throwIfAborted();
  onUpdate?.([]);
  const accepted = new Set<string>();
  const errors: unknown[] = [];
  let next = 0;
  const visible = () => items.filter(item => accepted.has(item.publicationId));
  async function checkNext() {
    while (next < items.length) {
      signal.throwIfAborted();
      const item = items[next++];
      try {
        const result = await libraryResponse(`/api/published-templates/${encodeURIComponent(item.publicationId)}/availability?sha256=${item.sha256.toLowerCase()}`, signal);
        signal.throwIfAborted();
        if (!isRecord(result) || typeof result.available !== "boolean") throw new Error(errorMessages.LIBRARY_INVALID_RESPONSE);
        if (result.available) {
          accepted.add(item.publicationId);
          onUpdate?.(visible());
        }
      } catch (error) {
        signal.throwIfAborted();
        errors.push(error);
      }
    }
  }
  // Bound requests so opening the library cannot flood the CAD worker queue.
  const outcomes = await Promise.allSettled([checkNext(), checkNext()]);
  signal.throwIfAborted();
  for (const outcome of outcomes) if (outcome.status === "rejected") throw outcome.reason;
  if (errors.length) throw errors[0];
  return visible();
}

export const publicationKey = (item: PublishedTemplate) => `${item.templateKind || "part"}:${item.publicationId}`;

export async function fetchReusableTemplates(signal: AbortSignal, onUpdate: (items: PublishedTemplate[]) => void): Promise<PublishedTemplate[]> {
  let parts: PublishedTemplate[] = [], components: PublishedTemplate[] = [];
  const merged = () => [...parts, ...components].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  onUpdate([]);
  const outcomes = await Promise.allSettled([
    fetchAvailableTemplates(signal, items => { parts = items; onUpdate(merged()); }),
    libraryResponse("/api/published-components", signal).then(body => {
      signal.throwIfAborted();
      const items = parsePublishedTemplates(body);
      if (items.some(item => item.templateKind !== "component")) throw new Error(errorMessages.LIBRARY_INVALID_RESPONSE);
      components = items;
      onUpdate(merged());
    }),
  ]);
  signal.throwIfAborted();
  for (const outcome of outcomes) if (outcome.status === "rejected") throw outcome.reason;
  return merged();
}

export type CachedLibrary = {
  items: PublishedTemplate[]; updateCount: number; initialized: boolean;
  syncing: boolean; checkedAt: number | null; error: string;
};

export async function readCachedLibrary(signal: AbortSignal, action?: "sync" | "apply"): Promise<CachedLibrary> {
  const response = await fetch(`/api/template-library${action ? `/${action}` : ""}`, {
    method: action ? "POST" : "GET", signal, cache: "no-store", headers: { Accept: "application/json" },
  });
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error(errorMessages.LIBRARY_INVALID_RESPONSE); }
  if (!response.ok) throw new Error("暂时无法读取模板列表，请稍后重试。");
  if (!isRecord(body) || typeof body.initialized !== "boolean" || typeof body.syncing !== "boolean" ||
    typeof body.updateCount !== "number" || !Number.isSafeInteger(body.updateCount) || body.updateCount < 0 ||
    !(body.checkedAt === null || typeof body.checkedAt === "number" && Number.isFinite(body.checkedAt)) ||
    !Array.isArray(body.errors) || body.errors.some(code => typeof code !== "string")) throw new Error(errorMessages.LIBRARY_INVALID_RESPONSE);
  const items = parsePublishedTemplates(body);
  return { items, initialized: body.initialized, syncing: body.syncing, updateCount: body.updateCount,
    checkedAt: body.checkedAt as number | null,
    error: [...new Set(body.errors.map(code => errorMessages[String(code)] || "暂时无法检查模板更新，请稍后重试。"))].join(" "),
  };
}
