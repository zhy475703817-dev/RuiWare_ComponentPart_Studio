import { useState } from "react";
import type { ComponentDocument, ComponentPort } from "../domain/component";
import type { DraftCommand, PortDiagnostic } from "../app/sharedDraft";

export function PortBasisPanel({ document, port, diagnostic, onExecute, onLocate }: {
  document: ComponentDocument; port: ComponentPort; diagnostic: PortDiagnostic;
  onExecute: (action: DraftCommand, revision?: number, reason?: string) => Promise<unknown>;
  onLocate: () => void;
}) {
  const member = document.assembly.members.find(item => item.id === port.source.memberId);
  const source = member?.interfaces.find(item => item.id === port.source.interfaceId);
  const [origin, setOrigin] = useState(source?.frame?.origin.map(String) || ["", "", ""]);
  const [rotation, setRotation] = useState(source?.frame?.rotation.map(value => String(value * 180 / Math.PI)) || ["", "", ""]);
  const [positionChecked, setPositionChecked] = useState(false);
  const [directionChecked, setDirectionChecked] = useState(false);
  const [note, setNote] = useState("");
  const [baseRevision] = useState(document.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const stale = document.revision !== baseRevision;
  const run = async (action: DraftCommand, reason: string) => {
    setBusy(true); setError("");
    try { if (!await onExecute(action, baseRevision, reason)) setError("保存未完成，请查看操作与诊断，重新读取后重试。"); }
    finally { setBusy(false); }
  };
  return <section className="port-basis" aria-label="接口基准确认">
    <p className="hint">模型中 Z 轴表示连接法向，X 轴确定绕法向的朝向；坐标是当前组件坐标。点击定位后核对连接面与轴向。</p>
    <button className="ghost" disabled={!source} onClick={onLocate}>在三维中定位基准</button>
    <dl><dt>组件连接点 · mm</dt><dd>{diagnostic.frame?.origin.map(value => value.toFixed(3)).join(" / ") || "缺少来源坐标"}</dd>
      {(["z", "x"] as const).map(axis => <div key={axis}><dt>{axis.toUpperCase()} {axis === "z" ? "连接法向" : "朝向"} · 单位向量</dt><dd>{diagnostic.axes?.[axis].map(value => value.toFixed(4)).join(" / ") || "未确定"}</dd></div>)}</dl>
    {port.basisConfirmation && <p className="hint">确认于 R{port.basisConfirmation.revision} · {new Date(port.basisConfirmation.createdAt).toLocaleString()}<br/>{port.basisConfirmation.note}</p>}
    {diagnostic.sourceChanges?.length ? <p className="port-warning">发生变化：{diagnostic.sourceChanges.join("、")}。原确认保留作历史依据，需要重新核对。</p> : null}
    <details><summary>补齐 / 修正当前草稿的来源基准</summary>
      <p className="hint">只修改本组件草稿中该成员的接口基准，发布包保持原样；修改后需重新确认并试配；这里不会修改正式装配位姿。</p>
      {([["局部连接点 · mm", origin, setOrigin], ["局部旋转 · ° XYZ", rotation, setRotation]] as const).map(([label, values, setValues]) => <fieldset key={label}><legend>{label}</legend><div className="port-three-columns">{["X", "Y", "Z"].map((axis, index) => <label key={axis}>{axis}<input aria-label={`${label} ${axis}`} type="number" step="any" value={values[index]} placeholder="未确定" onChange={event => setValues(values.map((value, i) => i === index ? event.target.value : value))}/></label>)}</div></fieldset>)}
      <button className="ghost" disabled={!source || stale || busy || [...origin, ...rotation].some(value => !value.trim() || !Number.isFinite(Number(value)))} onClick={() => void run({ type: "interface.set", ...port.source, frame: { origin: origin.map(Number) as [number, number, number], rotation: rotation.map(value => Number(value) * Math.PI / 180) as [number, number, number] } }, "人工修正当前草稿的来源接口基准")}>保存来源基准</button>
    </details>
    <label className="port-checkbox"><input type="checkbox" checked={positionChecked} onChange={event => setPositionChecked(event.target.checked)}/>已核对连接点位置与连接面</label>
    <label className="port-checkbox"><input type="checkbox" checked={directionChecked} onChange={event => setDirectionChecked(event.target.checked)}/>已核对 Z 法向与 X 朝向</label>
    <label>基准确认依据<textarea value={note} maxLength={2000} placeholder="例如：连接点位于挂钩接触面中心，Z 指向立柱，X 沿横梁" onChange={event => setNote(event.target.value)}/></label>
    <button className="primary" disabled={!diagnostic.frame || !positionChecked || !directionChecked || !note.trim() || stale || busy} onClick={() => void run({ type: "port.confirm", id: port.id, scope: "basis", note: note.trim() }, note.trim())}>确认当前接口基准</button>
    {stale && <p role="status">草稿已更新，请关闭并重新打开基准面板后确认。</p>}{error && <p role="alert" className="port-warning">{error}</p>}
  </section>;
}
