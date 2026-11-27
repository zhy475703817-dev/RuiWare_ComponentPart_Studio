import { useState } from "react";
import type { ComponentDocument, Vector3 } from "../domain/component";
import type { useGeometryResize } from "../app/useGeometryResize";
import { BraceGenerationWindow } from "./BraceGenerationWindow";

export function GeometryResizePanel({ resize, document, draftId }: { resize: ReturnType<typeof useGeometryResize>; document: ComponentDocument; draftId: string | null }) {
  const [a, setA] = useState(""), [b, setB] = useState("");
  const { job, inspection, dimensions, locks, relations } = resize;
  const endpoints = inspection?.interfaces || [];
  return <fieldset className="geometry-resize" aria-label="几何联动" disabled={!resize.enabled || resize.applying}>
    <h4>几何联动</h4>
    <p className="hint">按真实外形尺寸调整零件，保持底面及水平中心。先预览，应用后一次保存。</p>
    <BraceGenerationWindow document={document} draftId={draftId} resize={resize}/>
    <button className="ghost" type="button" disabled={job?.status === "running" || resize.applying} onClick={() => void resize.start(true)}>读取当前实际尺寸</button>
    {inspection && <p>当前外形：{inspection.dimensions.map(v => v.toFixed(2)).join(" × ")} mm</p>}
    {inspection?.parameterDifferences?.length ? <details><summary>历史尺寸参数与实际模型不同</summary><p>此次使用实际模型作为基线，读取不会修改模型。</p>{inspection.parameterDifferences.map(d => <p key={d.parameterId}>{d.parameterId}：历史参数 {d.saved.toFixed(2)} mm，实际对应轴 {d.actual.toFixed(2)} mm</p>)}</details> : null}
    {dimensions && <div className="resize-dimensions">{["目标长度 X", "目标宽度 Y", "目标高度 Z"].map((label, index) => <label key={label}>{label}<input aria-label={label} type="number" step="any" min="0.001" value={Number.isNaN(dimensions[index]) ? "" : dimensions[index]} disabled={resize.applying} onChange={e => { const value = [...dimensions] as Vector3; value[index] = e.currentTarget.valueAsNumber; resize.editDimensions(value); }}/><small>mm</small></label>)}</div>}
    {inspection && <details><summary>锁定零件或尺寸</summary>{inspection.members?.map(member => <div key={JSON.stringify(member.path)}><b>{member.name}</b><small>{member.path.join(" / ")}</small>{[{ id: null, label: "整个零件（位置和全部尺寸）" }, ...member.parameters.filter(p => ["number", "integer"].includes(p.valueType))].map(p => {
      const selected = locks.some(l => JSON.stringify(l.path) === JSON.stringify(member.path) && (l.parameterId || null) === p.id);
      return <label key={p.id || "all"}><input type="checkbox" checked={selected} onChange={e => resize.setLocks(e.target.checked ? [...locks, { path: member.path, parameterId: p.id }] : locks.filter(l => !(JSON.stringify(l.path) === JSON.stringify(member.path) && (l.parameterId || null) === p.id)))}/>{p.label}</label>;
    })}</div>)}</details>}
    {inspection && <details><summary>查看或补充联动关系（{relations.length}）</summary><p className="hint">保留两个接口当前的相对位置和方向；这是几何关系，不代表连接已通过工程检查。</p>
      {[['接口 A', a, setA], ['接口 B', b, setB]].map(([label, value, setter]) => <label key={String(label)}>{String(label)}<select value={String(value)} onChange={e => (setter as (value: string) => void)(e.target.value)}><option value="">选择真实接口</option>{endpoints.map((p, i) => <option key={i} value={i}>{p.path.join(" / ")} · {p.interfaceId}</option>)}</select></label>)}
      <button className="ghost" disabled={a === "" || b === "" || a === b} onClick={() => { resize.setRelations([...relations, { id: crypto.randomUUID(), a: endpoints[Number(a)], b: endpoints[Number(b)], mode: "preserve-offset", origin: "user", offset: [0, 0, 0] }]); setA(""); setB(""); }}>保留相对位置</button>
      {relations.map(r => <div key={r.id}><small>{r.a.path.join(" / ")} · {r.a.interfaceId} ↔ {r.b.path.join(" / ")} · {r.b.interfaceId}</small><button className="ghost" onClick={() => resize.setRelations(relations.filter(v => v.id !== r.id))}>移除</button></div>)}
    </details>}
    {job && <p role="status">{job.message}</p>}
    {resize.error && <p role="alert">{resize.error}</p>}
    {job?.error?.details.map((detail, i) => <div key={i} className="hint"><p>{detail.message || `${detail.path?.join(" / ") || ""} ${detail.parameterId || ""}`} {detail.minimum !== undefined ? `允许 ${detail.minimum} ～ ${detail.maximum}` : ""}</p>
      {detail.a && detail.candidates?.map((candidate, index) => <button key={index} className="ghost" onClick={() => { const next = [...relations, { id: crypto.randomUUID(), a: detail.a!, b: candidate, mode: "preserve-offset" as const, origin: "user" as const, offset: [0, 0, 0] as Vector3 }]; resize.setRelations(next); void resize.start(false, dimensions, locks, next); }}>{detail.a!.path.join(" / ")} · {detail.a!.interfaceId} ↔ {candidate.path.join(" / ")} · {candidate.interfaceId}</button>)}
      {detail.solutionIndex !== undefined && <button className="ghost" onClick={() => void resize.start(false, dimensions, locks, relations, detail.solutionIndex)}>{detail.specifications?.map(s => `${s.path.join(" / ")} ${s.parameterId}=${s.value}`).join("；")} · 使用此方案</button>}
    </div>)}
    {job?.result?.document && <>
      <p>{job.status === "applied" ? "已保存外形" : "预览外形"}：{job.result.dimensions.map(v => v.toFixed(2)).join(" × ")} mm</p>
      <p>调整 {job.result.changes?.length || 0} 个已有零件；新增 {job.result.structureChanges?.added.length || 0} 个，移除 {job.result.structureChanges?.removed.length || 0} 个。</p>
      <p className="hint">调整和新增的零件在预览中以浅蓝色标出；移除的零件见下方清单。</p>
      {job.result.structureChanges && (['added', 'removed'] as const).map(action => job.result!.structureChanges![action].length > 0 && <details key={action}>
        <summary>{action === 'added' ? '新增零件' : '移除零件'}（{job.result!.structureChanges![action].length}）</summary>
        {job.result!.structureChanges![action].map(item => <p key={JSON.stringify(item.path)}>{item.name}<small>{item.path.join(" / ")}</small></p>)}
      </details>)}
      {job.result.changes?.map(c => <details key={JSON.stringify(c.path)}><summary>{c.name}</summary><small>{c.path.join(" / ")}</small>{Object.entries(c.after.parameterValues).filter(([k, v]) => v !== c.before.parameterValues[k]).map(([k, v]) => <p key={k}>{k}：{String(c.before.parameterValues[k])} → {typeof v === "number" ? Number(v.toFixed(3)) : String(v)}</p>)}<p>位置：{c.before.position.map(v => v.toFixed(2)).join(", ")} → {c.after.position.map(v => v.toFixed(2)).join(", ")}</p><p>角度：{c.before.rotation.map(v => (v * 180 / Math.PI).toFixed(2)).join(", ")}° → {c.after.rotation.map(v => (v * 180 / Math.PI).toFixed(2)).join(", ")}°</p></details>)}
      {job.result.diagnostics?.length ? <details><summary>几何信息提示</summary>{job.result.diagnostics.map((d, i) => <p key={i}>{d.message}</p>)}</details> : null}
    </>}
    <div className="resize-actions"><button className="ghost" disabled={!dimensions || resize.applying} onClick={() => void resize.retry()}>重新预览</button><button className="primary" disabled={!resize.preview || resize.applying} onClick={() => void resize.apply()}>{resize.applying ? "正在保存…" : "应用"}</button><button className="ghost" disabled={resize.applying} onClick={resize.cancel}>取消</button>{resize.canUndo && <button className="ghost" disabled={resize.applying} onClick={() => void resize.restore()}>恢复应用前状态</button>}</div>
  </fieldset>;
}
