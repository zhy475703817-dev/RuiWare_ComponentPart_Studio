export type PublishedTemplate = {
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
  const invalid = () => new Error("发布库返回的数据格式不正确，请检查零部件平台接口。");
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
      !Number.isFinite(Date.parse(item.createdAt as string)) || ids.has(item.publicationId as string)
    ) throw invalid();
    ids.add(item.publicationId as string);
    return item as PublishedTemplate;
  });
  return items.sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

const errorMessages: Record<string, string> = {
  COMPONENT_LIBRARY_NOT_CONFIGURED: "尚未配置发布库连接密钥，请按接入说明完成本机配置后重启组件平台。",
  COMPONENT_LIBRARY_INVALID_URL: "发布库服务地址配置不正确，请检查本机配置后重启组件平台。",
  COMPONENT_LIBRARY_INVALID_KEY: "发布库连接密钥格式不正确，请使用不含换行的英文字符密钥并重启组件后端。",
  PUBLIC_API_NOT_CONFIGURED: "零部件平台尚未启用发布库读取接口，请配置接口密钥后重启零部件平台。",
  PUBLIC_API_KEY_INVALID: "发布库连接密钥不正确，请确认两个平台配置的密钥一致。",
  REMOTE_STORAGE_NOT_CONFIGURED: "零部件平台尚未配置共享发布库，请先完成共享存储配置。",
  REMOTE_STORAGE_UNAVAILABLE: "共享发布库暂时无法访问，请检查存储服务和网络后重试。",
  LIBRARY_UNREACHABLE: "无法连接零部件平台，请确认零部件平台已启动且服务地址正确。",
  LIBRARY_TIMEOUT: "读取发布库超时，请稍后重试。",
  LIBRARY_INVALID_RESPONSE: "发布库返回的数据格式不正确，请检查零部件平台接口。",
  LIBRARY_UPSTREAM_ERROR: "发布库服务暂时不可用，请稍后重试。",
};

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
  const items = await fetchPublishedTemplates(signal);
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
