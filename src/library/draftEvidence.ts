import type { EvidenceFile } from "../domain/component.ts";

const megabyte = 1024 * 1024;

export function draftEvidenceUrl(draftId: string, id: string) {
  return `/api/component-drafts/${encodeURIComponent(draftId)}/evidence/${encodeURIComponent(id)}`;
}

export async function uploadDraftEvidence(draftId: string, id: string, file: File): Promise<EvidenceFile> {
  const query = new URLSearchParams({ fileName: file.name });
  const response = await fetch(`${draftEvidenceUrl(draftId, id)}?${query}`, {
    method: "PUT", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result?.error?.code || "文件上传失败");
  return result;
}

export async function deleteDraftEvidence(draftId: string, id: string): Promise<void> {
  const response = await fetch(`${draftEvidenceUrl(draftId, id)}?unreferencedOnly=true`, { method: "DELETE" });
  if (!response.ok) throw new Error("未登记的附件清理失败");
}

export async function uploadDraftEvidenceFiles(draftId: string, files: File[], existing: EvidenceFile[]) {
  const additions: EvidenceFile[] = [];
  let total = existing.reduce((sum, item) => sum + item.size, 0);
  let message = "";
  for (const file of files) {
    if (file.size > 5 * megabyte) {
      message = `${file.name} 超过单文件 5 MB 限制`;
      continue;
    }
    if (existing.length + additions.length >= 10 || total + file.size > 15 * megabyte) {
      message = "每个草稿最多添加 10 个文件，总大小不超过 15 MB";
      break;
    }
    try {
      const uploaded = await uploadDraftEvidence(draftId, crypto.randomUUID(), file);
      additions.push(uploaded);
      total += uploaded.size;
    } catch (error) {
      message = error instanceof Error ? `上传失败：${error.message}` : "文件上传失败";
    }
  }
  return { additions, message };
}

export async function readDraftEvidence(draftId: string, file: EvidenceFile): Promise<EvidenceFile> {
  const response = await fetch(draftEvidenceUrl(draftId, file.id), { cache: "no-store" });
  if (!response.ok) throw new Error("附件读取失败");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length !== file.size) throw new Error("附件大小校验失败");
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const hash = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
  if (hash !== file.sha256) throw new Error("附件内容校验失败");

  let binary = "";
  const chunkSize = 32 * 1024;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return { ...file, dataUrl: `data:${file.mediaType};base64,${btoa(binary)}` };
}
