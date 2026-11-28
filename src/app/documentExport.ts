import type { ComponentDocument, EvidenceFile } from "../domain/component.ts";
import { readDraftEvidence } from "../library/draftEvidence.ts";

export async function embedDraftEvidence(
  document: ComponentDocument,
  load: (file: EvidenceFile) => Promise<EvidenceFile>,
): Promise<ComponentDocument> {
  const evidenceFiles = await Promise.all(document.definition.evidenceFiles.map(async file => {
    if (file.dataUrl) return file;
    try {
      const { dataUrl } = await load(file);
      if (!dataUrl) throw new Error("附件内容不可用");
      return { ...file, dataUrl };
    } catch (error) {
      throw new Error(`无法读取附件「${file.fileName}」`, { cause: error });
    }
  }));
  return { ...document, definition: { ...document.definition, evidenceFiles } };
}

function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadComponentExport(exported: unknown) {
  const name = (exported as { name?: unknown })?.name;
  const filename = typeof name === "string" ? name.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_") : "";
  downloadJson(exported, `${filename || "组件模板"}.component.json`);
}

export async function downloadDraftBackup(draftId: string | null, document: ComponentDocument) {
  const snapshot = structuredClone(document);
  const backup = await embedDraftEvidence(snapshot, file => {
    if (!draftId) throw new Error("草稿尚未就绪");
    return readDraftEvidence(draftId, file);
  });
  downloadJson(backup, "组件草稿-本地未保存副本.json");
}
