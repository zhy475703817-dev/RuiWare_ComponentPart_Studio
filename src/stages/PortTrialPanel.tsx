import { useEffect, useRef, useState } from "react";
import type { ComponentDocument, ComponentPort, PortTrialOptions, PortTrialPreview } from "../domain/component";
import type { DraftEnvelope, DraftSummary, PortDiagnostic } from "../app/sharedDraft";
import { httpDraftApi } from "../app/sharedDraft";

async function previewPorts(draftId: string, revision: number, portId: string, options: PortTrialOptions, signal: AbortSignal): Promise<PortTrialPreview> {
  const response = await fetch(`/api/component-drafts/${encodeURIComponent(draftId)}/ports/trial`, {
    method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
    body: JSON.stringify({ expectedRevision: revision, portId, options }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`${data.error?.message || "试配请求失败"} [${data.error?.code || response.status}]`);
  return data;
}

export function PortTrialPanel({ draftId, document, port, diagnostic, targets, onPreview, onRecord, onReview }: {
  draftId: string; document: ComponentDocument; port: ComponentPort; diagnostic: PortDiagnostic; targets: DraftSummary[];
  onPreview: (preview: PortTrialPreview | null) => void;
  onRecord: (options: PortTrialOptions, revision: number) => Promise<boolean>;
  onReview: (note: string, revision: number) => Promise<boolean>;
}) {
  const [targetId, setTargetId] = useState(port.trial?.options.targetDraftId || "");
  const [target, setTarget] = useState<DraftEnvelope | null>(null);
  const [targetPortId, setTargetPortId] = useState(port.trial?.options.targetPortId || "");
  const [offset, setOffset] = useState(String(port.trial?.options.offset ?? 0));
  const [angle, setAngle] = useState(String((port.trial?.options.angle ?? 0) * 180 / Math.PI));
  const [flip, setFlip] = useState(port.trial?.options.flip ?? true);
  const [preview, setPreview] = useState<PortTrialPreview | null>(null);
  const [note, setNote] = useState("");
  const [acceptedLimit, setAcceptedLimit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const sourceRevision = document.revision;
  const targetRevision = target?.document.revision;
  useEffect(() => {
    let cancelled = false;
    setTarget(null); setError("");
    if (!targetId || !expanded) return;
    const load = () => httpDraftApi.get(targetId).then(data => {
      if (cancelled) return;
      setTarget(data);
      setTargetPortId(id => data.document.ports.some(item => item.id === id) ? id : data.document.ports[0]?.id || "");
    }).catch(cause => { if (!cancelled) { setError((cause as Error).message); setTarget(null); } });
    void load();
    const timer = window.setInterval(load, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [targetId, reload, expanded]);
  useEffect(() => {
    generation.current++; request.current?.abort(); setPreview(null); setBusy(false); onPreview(null);
    return () => { generation.current++; request.current?.abort(); };
  }, [sourceRevision, targetId, targetRevision, targetPortId, offset, angle, flip, onPreview]);
  const options = (): PortTrialOptions | null => !target || !targetPortId || !offset.trim() || !angle.trim() || !Number.isFinite(Number(offset)) || !Number.isFinite(Number(angle)) ? null : ({
    targetDraftId: targetId, targetPortId, expectedTargetRevision: target.document.revision, relation: "固定",
    offset: Number(offset), angle: Number(angle) * Math.PI / 180, flip,
  });
  const runTrial = async () => {
    const selected = options(); if (!selected) return;
    const token = ++generation.current;
    request.current?.abort(); const abort = new AbortController(); request.current = abort;
    setBusy(true); setError(""); setPreview(null); onPreview(null);
    try {
      const result = await previewPorts(draftId, sourceRevision, port.id, selected, abort.signal);
      if (token !== generation.current) return;
      setPreview(result); onPreview(result);
    } catch (cause) { if (token === generation.current && !abort.signal.aborted) setError((cause as Error).message); }
    finally { if (token === generation.current) setBusy(false); }
  };
  const report = preview?.report || port.trial?.report;
  const runSave = async (review: boolean) => {
    const selected = options();
    setBusy(true); setError("");
    try {
      const ok = review ? await onReview(note.trim(), sourceRevision) : selected && preview && await onRecord(selected, preview.baseRevision);
      if (!ok) setError("保存未完成，请查看操作与诊断；如目标已更新，请重新读取并试配。");
      else { setNote(""); setAcceptedLimit(false); }
    } finally { setBusy(false); }
  };
  return <details className="port-trial" onToggle={event => { setExpanded(event.currentTarget.open); if (!event.currentTarget.open) { setPreview(null); onPreview(null); } }}><summary>对外试配 · {port.trial ? diagnostic.trialCurrent ? "当前报告可复核" : "报告需重检" : "尚未试配"}</summary>
    <p className="hint">选择另一个组件的对外接口，将整个本组件定位到目标坐标系中预览；保存报告仅保存检查依据。模型名称以“本侧”和“目标”区分，预览不会修改正式装配。</p>
    <label>目标组件草稿<select value={targetId} disabled={busy} onChange={event => { setTargetId(event.target.value); setTargetPortId(""); }}><option value="">请选择目标组件</option>{targets.filter(item => item.draftId !== draftId).map(item => <option key={item.draftId} value={item.draftId}>{item.name} · R{item.revision}</option>)}</select></label>
    <label>目标对外接口<select value={targetPortId} disabled={!target || busy} onChange={event => setTargetPortId(event.target.value)}><option value="">请选择目标接口</option>{target?.document.ports.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {target && <p className="hint">目标当前 R{target.document.revision} · 每 5 秒核对版本。{!target.document.ports.length && "目标尚未暴露接口，请先在该组件第三步添加。"}</p>}
    <div className="port-two-columns"><label>沿目标 Z 偏置 · mm<input type="number" step="any" value={offset} onChange={event => setOffset(event.target.value)}/></label><label>绕目标 Z 旋转 · °<input type="number" step="any" value={angle} onChange={event => setAngle(event.target.value)}/></label></div>
    <label className="port-checkbox"><input type="checkbox" checked={flip} onChange={event => setFlip(event.target.checked)}/>连接法向相对（翻转 X 180°）</label>
    <div className="action-row"><button className="primary" disabled={!options() || busy} onClick={() => void runTrial()}>{busy ? "处理中…" : "预览接口试配"}</button><button className="ghost" disabled={!targetId || busy} onClick={() => setReload(value => value + 1)}>重新读取目标</button>{preview && <button className="ghost" onClick={() => { setPreview(null); onPreview(null); }}>退出预览</button>}</div>
    {error && <p className="port-warning" role="alert">{error}</p>}
    {report && <section className="port-trial-report" aria-label="双边试配报告">
      <h5>{preview ? "本次试配报告" : "已保存的检查快照"}</h5>
      <p>规则：{report.rules.status === "pass" ? "通过" : report.rules.status === "fail" ? "不匹配" : "待补齐"} · 基准对齐：{report.alignment.status === "pass" ? "通过" : "待检查"} · 碰撞：未检查</p>
      <p className="port-warning">{report.collision.message}</p>
      {report.rules.dimensions.length > 0 && <div className="port-table-wrap"><table><caption>两侧尺寸 / 间隙 · mm</caption><thead><tr><th>尺寸标识</th><th>本侧</th><th>目标</th><th>间隙 ± 公差</th></tr></thead><tbody>{report.rules.dimensions.map(row => <tr key={row.key}><th>{row.key}</th><td>{row.movingMm ?? "未定"}</td><td>{row.targetMm ?? "未定"}</td><td>{row.clearanceMm?.toFixed(3) ?? "—"} ± {row.uncertaintyMm?.toFixed(3) ?? "—"}<small>{row.status}</small></td></tr>)}</tbody></table></div>}
      {report.rules.issues.length > 0 && <ul className="port-issues">{report.rules.issues.map((issue, i) => <li key={i}>{issue.message}<code>{issue.code} · {issue.status}</code></li>)}</ul>}
      <dl><dt>试配前偏差</dt><dd>{report.alignment.beforeDistanceMm?.toFixed(3) ?? "—"} mm / {report.alignment.beforeAngleRad === undefined ? "—" : (report.alignment.beforeAngleRad * 180 / Math.PI).toFixed(2)}°</dd><dt>试配后基准残差</dt><dd>{report.alignment.positionErrorMm?.toExponential(2) ?? "—"} mm / {report.alignment.angleErrorRad?.toExponential(2) ?? "—"} rad</dd></dl>
      <p className="hint">依据：本侧 R{preview?.baseRevision ?? port.trial?.revision} / 目标 R{preview?.targetRevision ?? port.trial?.options.expectedTargetRevision}。{!preview && !diagnostic.trialCurrent && "这是历史结果，不能用于当前版本结论。"}</p>
      {preview && <button className="ghost" disabled={busy} onClick={() => void runSave(false)}>保存本次试配报告</button>}
      <details><summary>检查数据与计算依据</summary><pre>{JSON.stringify(preview || port.trial, null, 2)}</pre></details>
    </section>}
    {diagnostic.trialCurrent && diagnostic.layers?.rules === "pass" && diagnostic.layers.basis === "pass" && <section className="port-human-review"><h5>人工复核</h5>
      {port.reviewConfirmation && <p className="hint">上次人工说明：{port.reviewConfirmation.note}</p>}
      <label className="port-checkbox"><input type="checkbox" checked={acceptedLimit} onChange={event => setAcceptedLimit(event.target.checked)}/>已检查规则和预览，知悉自动碰撞未执行</label>
      <label>复核依据与待办<textarea value={note} maxLength={2000} placeholder="记录核对依据、未验证项目及需补充的数据" onChange={event => setNote(event.target.value)}/></label>
      <button className="primary" disabled={!note.trim() || !acceptedLimit || busy} onClick={() => void runSave(true)}>记录人工复核</button>
    </section>}
  </details>;
}
