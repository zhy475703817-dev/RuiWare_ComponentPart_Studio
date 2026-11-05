import type { EvidenceAttachment } from "./component";

const databaseName = "ruiware-component-evidence";
const storeName = "attachments";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in window)) return reject(new Error("当前浏览器不支持本地附件存储"));
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(storeName);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开本地附件存储"));
  });
}

async function withStore<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode);
      const request = action(transaction.objectStore(storeName));
      let result: T;
      request.onsuccess = () => { result = request.result; };
      request.onerror = () => reject(request.error ?? new Error("本地附件操作失败"));
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () => reject(transaction.error ?? new Error("本地附件操作失败"));
    });
  } finally {
    database.close();
  }
}

export function saveEvidenceFile(id: string, file: Blob): Promise<IDBValidKey> {
  return withStore("readwrite", store => store.put(file, id));
}

export function deleteEvidenceFile(id: string): Promise<undefined> {
  return withStore("readwrite", store => store.delete(id));
}

export function readEvidenceFile(id: string): Promise<Blob | undefined> {
  return withStore("readonly", store => store.get(id));
}

export async function exportEvidenceFile(id: string): Promise<string | undefined> {
  const blob = await readEvidenceFile(id);
  if (!blob) return undefined;
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("读取附件失败"));
    reader.readAsDataURL(blob);
  });
}

export async function exportEvidenceFiles(evidence: EvidenceAttachment[]) {
  return Promise.all(evidence.map(async item => {
    const dataUrl = await exportEvidenceFile(item.id);
    if (!dataUrl) throw new Error(`附件 ${item.name} 在当前浏览器中不可用`);
    return { ...item, dataUrl };
  }));
}
