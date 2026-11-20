import { useEffect, useRef, useState } from "react";
import type { ComponentDocument, GeometryLock, GeometryRelation, Vector3 } from "../domain/component";

export type ResizeJob = {
  jobId: string; expectedRevision: number; status: "running" | "ready" | "needs_choice" | "failed" | "cancelled" | "stale" | "applied"; message: string;
  error?: { code: string; details: Array<{ message?: string; solutionIndex?: number; specifications?: Array<{ path: string[]; parameterId: string; value: number }>; path?: string[]; parameterId?: string; minimum?: number; maximum?: number; a?: { path: string[]; interfaceId: string }; candidates?: Array<{ path: string[]; interfaceId: string }> }> };
  result?: { document?: ComponentDocument; checksum?: string; dimensions: Vector3; originalDimensions?: Vector3;
    structureChanges?: { added: Array<{ path: string[]; name: string }>; removed: Array<{ path: string[]; name: string }> };
    changes?: Array<{ path: string[]; name: string; before: { parameterValues: Record<string, unknown>; position: Vector3; rotation: Vector3 }; after: { parameterValues: Record<string, unknown>; position: Vector3; rotation: Vector3 } }>;
    members?: Array<{ path: string[]; name: string; parameters: Array<{ id: string; label: string; valueType: string; minimum: number | null; maximum: number | null }> }>;
    interfaces?: Array<{ path: string[]; interfaceId: string }>; relations?: GeometryRelation[];
    diagnostics?: Array<{ message: string }>;
    parameterDifferences?: Array<{ parameterId: string; saved: number; actual: number }>;
  };
};

async function request<T>(url: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(30000);
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
    .catch(() => {
      if (timeout.aborted) throw new Error("后台响应超时，请重新读取状态后重试。");
      throw new Error("无法连接组件平台，请重新启动平台后重试。当前草稿不会因此改变。");
    });
  if ([502, 503, 504].includes(r.status)) throw new Error("组件后台暂时无法连接，请重新启动平台后重试。");
  const data = await r.json();
  if (!r.ok) throw new Error(data.error?.code === "REVISION_CONFLICT" ? "草稿已变化，请重新试算" : `几何联动操作失败：${data.error?.code || r.status}`);
  return data;
}

export function useGeometryResize(draftId: string | null, document: ComponentDocument, enabled: boolean, reload: () => void) {
  const [job, setJob] = useState<ResizeJob | null>(null);
  const [inspection, setInspection] = useState<ResizeJob["result"]>();
  const [dimensions, setDimensions] = useState<Vector3 | null>(null);
  const [locks, setLocks] = useState<GeometryLock[]>([]);
  const [relations, setRelations] = useState<GeometryRelation[]>([]);
  const [error, setError] = useState("");
  const [applying, setApplying] = useState(false);
  const [undo, setUndo] = useState<{ jobId: string; checksum: string; revision: number } | null>(null);
  const [braceOverride, setBraceOverride] = useState<boolean | null>(null);
  const braceModes = JSON.stringify([draftId, (document.braceGenerationRules || []).map(rule => [rule.id, rule.enabled !== false])]);
  useEffect(() => { setBraceOverride(null); }, [braceModes]);
  const autoFillBraces = braceOverride ?? (document.braceGenerationRules || []).some(rule => rule.enabled !== false);
  const generation = useRef(0), active = useRef<{ draft: string; id: string } | null>(null), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const polling = useRef<AbortController | null>(null);
  const base = `/api/component-drafts/${draftId}`;
  const cancel = () => {
    generation.current++; clearTimeout(timer.current);
    polling.current?.abort(); polling.current = null;
    const old = active.current; active.current = null;
    if (old) void request(`/api/component-drafts/${old.draft}/resize-jobs/${old.id}`, "DELETE").catch(() => {});
    setJob(null); setError("");
  };
  useEffect(() => {
    cancel(); setInspection(undefined); setDimensions(null); setLocks(document.geometryDesign?.locks || []); setRelations(document.geometryDesign?.relations || []); setUndo(null);
    return () => { generation.current++; clearTimeout(timer.current); polling.current?.abort(); const old = active.current; if (old) void request(`/api/component-drafts/${old.draft}/resize-jobs/${old.id}`, "DELETE").catch(() => {}); active.current = null; };
  }, [draftId]);
  useEffect(() => {
    if (job && job.expectedRevision !== document.revision && job.status !== "applied") cancel();
    if (job?.status !== "applied") { setInspection(undefined); setDimensions(null); }
    setLocks(document.geometryDesign?.locks || []); setRelations(document.geometryDesign?.relations || []);
  }, [document.revision]);
  useEffect(() => { if (!enabled && job?.status !== "applied") cancel(); }, [enabled]);
  useEffect(() => {
    if (!draftId || !enabled) return;
    let disposed = false;
    void request<{ undo: typeof undo }>(`${base}/resize-history`).then(result => { if (!disposed) setUndo(result.undo); }).catch(() => {});
    return () => { disposed = true; };
  }, [draftId, document.revision, enabled]);
  async function start(inspectOnly: boolean, target = dimensions, selectedLocks = locks, selectedRelations = relations, solutionIndex?: number, retryFailedTarget = false) {
    if (!draftId || !enabled || (!inspectOnly && !target)) return;
    cancel(); const token = generation.current;
    const controller = new AbortController(); polling.current = controller;
    setJob({ jobId: "", expectedRevision: document.revision, status: "running", message: inspectOnly ? "正在读取实际尺寸…" : "正在计算几何联动…" });
    try {
      let next = await request<ResizeJob>(`${base}/resize-jobs`, "POST", { expectedRevision: document.revision, inspectOnly, dimensions: inspectOnly ? undefined : target, autoFillBraces: inspectOnly ? undefined : braceOverride, design: { locks: selectedLocks, relations: selectedRelations }, solutionIndex, retryFailedTarget });
      if (token !== generation.current) { void request(`${base}/resize-jobs/${next.jobId}`, "DELETE").catch(() => {}); return; }
      active.current = { draft: draftId, id: next.jobId }; setJob(next);
      while (next.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 700));
        if (token !== generation.current) return;
        next = await request<ResizeJob>(`${base}/resize-jobs/${next.jobId}`, "GET", undefined, controller.signal);
        if (token !== generation.current) return;
        setJob(next);
      }
      if (inspectOnly && next.status === "ready" && next.result) { setInspection(next.result); setDimensions(next.result.dimensions.map(v => Number(v.toFixed(6))) as Vector3); }
      if (!inspectOnly && next.status === "ready" && next.result?.relations) setRelations(next.result.relations);
    } catch (e) {
      if (token === generation.current) {
        const old = active.current; active.current = null;
        controller.abort();
        if (old) void request(`/api/component-drafts/${old.draft}/resize-jobs/${old.id}`, "DELETE").catch(() => {});
        setError(e instanceof Error ? e.message : "计算失败"); setJob(null);
      }
    }
  }
  function editDimensions(value: Vector3) {
    cancel(); setDimensions(value);
    if (value.every(n => Number.isFinite(n) && n > 0)) timer.current = setTimeout(() => { void start(false, value); }, 650);
  }
  async function apply() {
    if (!job?.result?.document || !job.result.checksum || job.status !== "ready" || applying || !enabled) return;
    setApplying(true); setError("");
    try {
      const result = await request<{ document: ComponentDocument }>(`${base}/resize-apply`, "POST", { expectedRevision: document.revision, jobId: job.jobId, checksum: job.result.checksum });
      active.current = null; setUndo({ jobId: job.jobId, checksum: job.result.checksum, revision: result.document.revision });
      setInspection(previous => previous ? { ...previous, dimensions: job.result!.dimensions } : previous);
      setRelations(result.document.geometryDesign?.relations || []);
      setJob({ ...job, status: "applied", message: "几何联动已整体保存" }); reload();
    } catch (e) { setError(e instanceof Error ? e.message : "保存失败"); }
    finally { setApplying(false); }
  }
  async function restore() {
    if (!undo || applying || !enabled) return;
    setApplying(true);
    try { await request(`${base}/resize-undo`, "POST", { expectedRevision: document.revision, jobId: undo.jobId, checksum: undo.checksum }); setUndo(null); cancel(); setInspection(undefined); setDimensions(null); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "恢复失败"); }
    finally { setApplying(false); }
  }
  return { job, inspection, dimensions, locks, relations, error, applying, enabled, start, cancel, editDimensions, apply, restore,
    autoFillBraces,
    setAutoFillBraces: (value: boolean) => { cancel(); setBraceOverride(value); },
    previewHeight: (height: number) => {
      if (!inspection || !Number.isFinite(height) || height <= 0) return;
      const target: Vector3 = [inspection.dimensions[0], inspection.dimensions[1], height];
      setDimensions(target); void start(false, target);
    },
    retry: () => start(false, dimensions, locks, relations, undefined, true),
    canUndo: undo?.revision === document.revision,
    setLocks: (value: GeometryLock[]) => { cancel(); setLocks(value); },
    setRelations: (value: GeometryRelation[]) => { cancel(); setRelations(value); },
    preview: job?.status === "ready" && job.expectedRevision === document.revision ? job.result?.document : undefined };
}
