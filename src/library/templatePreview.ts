import { Vector3 } from "three";
import type { BufferGeometry } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { parsePublishedTemplates } from "./publishedLibrary.ts";
import type { PublishedTemplate } from "./publishedLibrary.ts";
import type { PartInterface } from "../domain/component.ts";

export type PreviewDescription = {
  publication: PublishedTemplate;
  unitSystem: "mm-kg-s";
  coordinateSystem: "right-handed-z-up";
  parameters: Record<string, unknown>[];
  interfaces: Record<string, unknown>[];
  model: { format: "stl"; source: "package" | "legacy"; url: string; sha256: string; size: number };
};
export type ReconstructionParameter = {
  id: string; label: string; unit: string; valueType: string; default: number | string | boolean;
  minimum: number | null; maximum: number | null; allowedValues: (number | string | boolean)[];
};
export type LoadedPreview = { description: PreviewDescription; geometry: BufferGeometry };
export type ReconstructionInfo = { publication: PublishedTemplate; parameters: ReconstructionParameter[]; interfaces: PartInterface[] };
export type PreviewPhase = "package" | "model";
const MAX_MODEL_BYTES = 64 * 1024 * 1024;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parsePreviewDescription(value: unknown, selected: PublishedTemplate): PreviewDescription {
  const invalid = () => new Error("模型预览信息不正确，请刷新发布库后重试。");
  if (!record(value) || !record(value.publication) || !record(value.model)) throw invalid();
  const publication = parsePublishedTemplates({ items: [value.publication], count: 1 })[0];
  if (publication.publicationId !== selected.publicationId || publication.templateId !== selected.templateId || publication.version !== selected.version || publication.sha256.toLowerCase() !== selected.sha256.toLowerCase()) throw invalid();
  const model = value.model;
  const expectedUrl = `/api/published-templates/${encodeURIComponent(selected.publicationId)}/preview/model?sha256=${selected.sha256.toLowerCase()}`;
  if (value.unitSystem !== "mm-kg-s" || value.coordinateSystem !== "right-handed-z-up" ||
    ![value.parameters, value.interfaces].every(items => Array.isArray(items) && items.every(record)) ||
    model.format !== "stl" || !["package", "legacy"].includes(String(model.source)) || model.url !== expectedUrl ||
    typeof model.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(model.sha256) ||
    typeof model.size !== "number" || !Number.isSafeInteger(model.size) || model.size < 1 || model.size > MAX_MODEL_BYTES) throw invalid();
  return value as PreviewDescription;
}

export function parseStlGeometry(data: ArrayBuffer): BufferGeometry {
  let geometry: BufferGeometry | undefined;
  try {
    if (!data.byteLength || data.byteLength > MAX_MODEL_BYTES) throw new Error();
    // Validate binary triangle count before STLLoader allocates its typed arrays.
    const binary = data.byteLength >= 84 && 84 + new DataView(data).getUint32(80, true) * 50 === data.byteLength;
    let input: ArrayBuffer | string = data;
    if (!binary) {
      input = new TextDecoder().decode(data).trimStart();
      if (!input.startsWith("solid")) throw new Error();
      // STLLoader re-detects ASCII using only the first five bytes.
      // Passing normalized text keeps malformed binary counts out of that path.
    }
    geometry = new STLLoader().parse(input);
    const positions = geometry.getAttribute("position");
    if (!positions || positions.count < 3 || positions.count % 3) throw new Error();
    for (let i = 0; i < positions.array.length; i++) if (!Number.isFinite(positions.array[i])) throw new Error();
    const a = new Vector3(), b = new Vector3(), c = new Vector3();
    let hasTriangle = false;
    for (let i = 0; i < positions.count; i += 3) {
      a.fromBufferAttribute(positions, i); b.fromBufferAttribute(positions, i + 1); c.fromBufferAttribute(positions, i + 2);
      if (b.sub(a).cross(c.sub(a)).lengthSq() > 0) { hasTriangle = true; break; }
    }
    if (!hasTriangle) throw new Error();
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    return geometry;
  } catch {
    geometry?.dispose();
    throw new Error("模型文件无法解析，或没有有效的三角形，请检查发布版本。");
  }
}

const messages: Record<string, string> = {
  MODEL_MISSING: "无法显示这个零件，请联系模板维护人员检查模板内容。",
  PACKAGE_UNSUPPORTED: "无法显示这个零件，请联系模板维护人员检查模板内容。",
  MODEL_RECONSTRUCTION_FAILED: "零件暂时无法显示，请稍后重试。",
  PARAMETER_INVALID: "参数超出该零件的可调整范围。",
  PACKAGE_INTEGRITY_ERROR: "模板包校验失败，请刷新发布库后重试。",
  MODEL_INTEGRITY_ERROR: "模型文件校验失败，请检查发布版本后重试。",
  PACKAGE_INVALID: "模板包内容不完整或格式不正确，请检查发布版本。",
  PACKAGE_TOO_LARGE: "模板包或模型超过当前预览大小限制。",
  PUBLICATION_CHANGED: "发布记录已变化，请刷新发布库后重新选择。",
  REMOTE_TEMPLATE_NOT_FOUND: "该发布版本已不存在，请刷新发布库。",
  REMOTE_STORAGE_INTEGRITY_ERROR: "共享发布库中的模板包校验失败。",
  LIBRARY_TIMEOUT: "读取零件超时，请重试。",
  LIBRARY_UNREACHABLE: "共享发布库暂时无法访问，请检查 MinIO 服务或网络连接后重试。",
  PUBLIC_API_KEY_INVALID: "发布库连接密钥不正确，请检查组件后端配置。",
  COMPONENT_LIBRARY_NOT_CONFIGURED: "组件后端尚未配置发布库连接密钥。",
};

async function responseError(response: Response): Promise<never> {
  let body: unknown;
  try { body = await response.json(); } catch { /* Use the safe generic message. */ }
  const code = record(body) && record(body.error) && typeof body.error.code === "string" ? body.error.code : "";
  throw new Error(messages[code] || "模型读取失败，请检查服务连接后重试。");
}

export async function loadReconstructionInfo(selected: PublishedTemplate, signal: AbortSignal): Promise<ReconstructionInfo> {
  const response = await fetch(`/api/published-templates/${encodeURIComponent(selected.publicationId)}/reconstruction?sha256=${selected.sha256.toLowerCase()}`, { signal, cache: "no-store" });
  if (!response.ok) return responseError(response);
  const value: unknown = await response.json();
  if (!record(value) || !record(value.publication) || !Array.isArray(value.parameters) || !Array.isArray(value.interfaces)) throw new Error("无法显示这个零件，请稍后重试。");
  const publication = parsePublishedTemplates({ items: [value.publication], count: 1 })[0];
  if (publication.publicationId !== selected.publicationId || publication.templateId !== selected.templateId || publication.sha256.toLowerCase() !== selected.sha256.toLowerCase()) throw new Error("发布版本已更新，请重新选择。");
  const parameters = value.parameters.map(item => {
    if (!record(item) || typeof item.id !== "string" || typeof item.label !== "string" || typeof item.unit !== "string" ||
      !["number", "integer", "string", "enum", "boolean"].includes(String(item.valueType)) ||
      !(typeof item.default === "number" || typeof item.default === "string" || typeof item.default === "boolean") ||
      !(item.minimum === null || typeof item.minimum === "number") || !(item.maximum === null || typeof item.maximum === "number") ||
      !Array.isArray(item.allowedValues)) throw new Error("无法读取零件参数。");
    return item as ReconstructionParameter;
  });
  const interfaceIds = new Set<string>();
  const interfaces = value.interfaces.map(item => {
    if (!record(item) || typeof item.id !== "string" || !item.id.trim() || typeof item.name !== "string" || !item.name.trim() || interfaceIds.has(item.id)) {
      throw new Error("无法读取零件接口。");
    }
    const sourceType = typeof item.type === "string" ? item.type : "";
    const locatingType = typeof item.locatingType === "string" ? item.locatingType : "";
    const interfaceType = typeof item.interfaceType === "string" ? item.interfaceType : "";
    const type: PartInterface["type"] = sourceType === "plane" || sourceType === "axis" || sourceType === "point"
      ? sourceType
      : locatingType === "axisCoincident" || interfaceType === "connecting" ? "axis"
        : locatingType === "pinHole" ? "point" : "plane";
    const rawTags = Array.isArray(item.tags) ? item.tags : Array.isArray(item.compatibilityTags) ? item.compatibilityTags : [];
    const tags = [...new Set(rawTags.filter((tag): tag is string => typeof tag === "string" && Boolean(tag.trim())).map(tag => tag.trim()))];
    if (interfaceType) tags.push(`interface:${interfaceType}`);
    if (locatingType) tags.push(`locating:${locatingType}`);
    interfaceIds.add(item.id);
    return { id: item.id, name: item.name, type, tags: [...new Set(tags)] };
  });
  return { publication, parameters, interfaces };
}

export async function loadTemplatePreview(selected: PublishedTemplate, parameters: Record<string, unknown>, signal: AbortSignal): Promise<LoadedPreview> {
  signal.throwIfAborted();
  const response = await fetch(`/api/published-templates/${encodeURIComponent(selected.publicationId)}/reconstruction/model?sha256=${selected.sha256.toLowerCase()}`, {
    method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ parameters }),
  });
  if (!response.ok) return responseError(response);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("零件文件读取失败，请重试。");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_MODEL_BYTES) throw new Error("零件文件超过当前预览大小限制。");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const actualHash = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
  const responseHash = response.headers.get("x-ruiware-sha256");
  if (responseHash && actualHash !== responseHash.toLowerCase()) throw new Error("零件文件校验失败，请重试。");
  signal.throwIfAborted();
  const description: PreviewDescription = {
    publication: selected, unitSystem: "mm-kg-s", coordinateSystem: "right-handed-z-up", parameters: [], interfaces: [],
    model: { format: "stl", source: "package", url: "", sha256: actualHash, size },
  };
  return { description, geometry: parseStlGeometry(bytes.buffer) };
}
