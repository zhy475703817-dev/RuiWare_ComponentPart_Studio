import { useEffect, useRef, useState } from "react";
import type { Vector3 } from "../domain/component";

export type QualificationConfig = { axes: number[] | null; minimumDimensions: Vector3 | null; maximumDimensions: Vector3 | null; targets: Vector3[] };
export type Qualification = {
  revision: number; status: string; message: string; detail?: string; deliveryReady: boolean;
  config: QualificationConfig; axes?: number[]; nominalDimensions?: number[];
  minimumDimensions?: number[]; maximumDimensions?: number[]; scope?: string;
  automaticRecoveries?: number; nominalRepairCount?: number;
  error?: { code: string; details: Array<{ message?: string }> };
  cases: Array<{ label: string; dimensions: number[]; status: string; attempts?: number; error?: { message: string } }>;
};

export function useQualification(draftId: string | null, revision: number, enabled: boolean, reload: () => void) {
  const [report, setReport] = useState<Qualification | null>(null);
  const [error, setError] = useState("");
  const reloadRef = useRef(reload); reloadRef.current = reload;
  const refreshed = useRef("");
  const active = useRef(draftId); active.current = draftId;
  const currentRevision = useRef(revision); currentRevision.current = revision;
  const base = `/api/component-drafts/${draftId}/qualification`;
  async function request(method: string, body?: unknown, suffix = "") {
    const response = await fetch(base + suffix, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.code === "REVISION_CONFLICT" ? "草稿已更新，请等待同步后重试。" : "制作验收操作失败，请检查使用范围后重试。");
    if (active.current === draftId) { setReport(data); setError(""); }
    return data as Qualification;
  }
  async function start(retry = false) {
    try { return await request("POST", { expectedRevision: revision, retry }); }
    catch (e) { if (active.current === draftId) setError(e instanceof Error ? e.message : "验收启动失败"); }
  }
  async function configure(config: QualificationConfig) {
    try { await request("PUT", { expectedRevision: revision, config }, "/config"); await start(); }
    catch (e) { if (active.current === draftId) setError(e instanceof Error ? e.message : "使用范围保存失败"); }
  }
  async function cancel() {
    try { await request("DELETE"); }
    catch (e) { if (active.current === draftId) setError(e instanceof Error ? e.message : "暂停失败"); }
  }
  useEffect(() => { setReport(null); setError(""); }, [draftId]);
  useEffect(() => {
    if (!draftId || !enabled) return;
    let disposed = false;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch(base, { cache: "no-store", signal: abort.signal });
        if (!response.ok) throw new Error("无法读取制作验收进度");
        const data = await response.json() as Qualification;
        if (disposed) return;
        setReport(data); setError("");
        if (data.revision > currentRevision.current && refreshed.current !== `${draftId}:${data.revision}`) {
          refreshed.current = `${draftId}:${data.revision}`; reloadRef.current();
        }
        if (["pending", "stale"].includes(data.status) && data.revision === currentRevision.current) {
          await start();
        }
      } catch (e) { if (!disposed) setError(e instanceof Error ? e.message : "验收状态读取失败"); }
      if (!disposed) timer = setTimeout(() => void poll(), 2500);
    }
    timer = setTimeout(() => void poll(), 1200);
    return () => { disposed = true; abort.abort(); clearTimeout(timer); };
  }, [draftId, revision, enabled]);
  return { report, error, start, configure, cancel,
    busy: report?.status === "queued" || report?.status === "running",
    deliveryReady: report?.revision === revision && report.deliveryReady === true };
}
