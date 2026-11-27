import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, Layers, X } from "lucide-react";
import type { ComponentDocument } from "../domain/component";
import type { useGeometryResize } from "../app/useGeometryResize";
import { hasBuiltInBraces } from "../domain/bracePolicy";
import "../styles/brace-generation.css";

type Props = {
  document: ComponentDocument;
  draftId: string | null;
  resize: ReturnType<typeof useGeometryResize>;
};

export function BraceGenerationWindow({ document, draftId, resize }: Props) {
  const [open, setOpen] = useState(false);
  const rules = document.braceGenerationRules || [];
  return <section className="brace-entry" aria-label="柱片高度与撑杆自动补齐">
    <div className="brace-entry-title"><Layers size={16}/><strong>柱片高度与撑杆</strong></div>
    <p className="hint">拉长或缩短柱片，选择是否按真实孔位自动补齐横撑、斜撑。</p>
    <small>{rules.length ? `模板自带 ${rules.length} 套规则 · ${resize.autoFillBraces ? "自动补齐已选择" : "自动补齐未选择"}` : "模板尚未完成内置补齐验收"}</small>
    <button type="button" className="ghost" onClick={() => setOpen(true)}>打开柱片调整窗口</button>
    {open && createPortal(<BraceDialog key={draftId} {...{ document, draftId, resize }} onClose={() => setOpen(false)}/>, window.document.body)}
  </section>;
}

function BraceDialog({ document, resize, onClose }: Props & { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [height, setHeight] = useState("");
  const rules = document.braceGenerationRules || [];
  const running = resize.job?.status === "running";
  const busy = resize.applying;
  const current = resize.inspection?.dimensions[2];
  const target = Number(height);
  const valid = height.trim() !== "" && Number.isFinite(target) && target > 0;
  const needsRules = !hasBuiltInBraces(document) || resize.autoFillBraces && rules.length === 0;
  const parameter = (id: string) => document.parameters.find(item => item.id === id);
  const member = (id: string) => document.assembly.members.find(item => item.id === id)?.name || id;
  const step = rules.length ? (parameter(rules[0].pitchParameterId)?.value || 0) * (rules[0].columnInterfaceIndexStep ?? 1) : 100;
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    if (current !== undefined) setHeight(String(Number(current.toFixed(3))));
  }, [current]);
  const changeHeight = (value: string) => { resize.cancel(); setHeight(value); };
  const close = () => { resize.cancel(); onClose(); };
  return <dialog ref={dialog} className="brace-dialog" aria-labelledby="brace-dialog-title" onCancel={event => { event.preventDefault(); if (!resize.applying) close(); }}>
    <header><div><span className="brace-eyebrow">几何联动</span><h2 id="brace-dialog-title">柱片高度与撑杆自动补齐</h2></div><button type="button" className="icon-button" aria-label="关闭柱片调整窗口" disabled={resize.applying} onClick={close}><X size={20}/></button></header>
    <div className="brace-dialog-body">
      <section className="brace-mode">
        <label><input type="checkbox" checked={resize.autoFillBraces} disabled={busy || !resize.enabled || rules.length === 0} onChange={event => resize.setAutoFillBraces(event.target.checked)}/><span><strong>自动补齐横撑、斜撑</strong><small>模板内置规则，升高补齐、降低移除多余撑杆，保持原规格与长度。</small></span></label>
        {!resize.autoFillBraces && !needsRules && <p>本次使用普通几何联动，保留现有撑杆数量；撑杆尺寸与位置按已有约束求解。</p>}
        {(needsRules || rules.length === 0) && <p role="alert" className="brace-warning">此模板尚未完成内置补齐规则的制作与验收，暂不能自动补齐。新柱片须在制作阶段完成，旧模板须先完成升级验收。</p>}
      </section>
      <section className="brace-height-section">
        <div className="brace-section-heading"><h3>拉长或缩短柱片</h3><button type="button" className="ghost" disabled={busy || !resize.enabled} onClick={() => void resize.start(true)}>{running && !resize.dimensions ? "正在读取…" : "读取当前实际尺寸"}</button></div>
        <p className="brace-current">当前高度 <strong>{current === undefined ? "待读取" : `${current.toFixed(2)} mm`}</strong></p>
        <label className="brace-height-label">目标高度 Z（mm）<input type="number" min="0.001" step="any" value={height} disabled={busy || !resize.enabled || current === undefined} onChange={event => changeHeight(event.target.value)} placeholder="先读取当前实际尺寸"/></label>
        <div className="brace-height-actions">
          <button type="button" className="ghost" disabled={busy || !resize.enabled || current === undefined} onClick={() => changeHeight(String(Number(((valid ? target : current!) + (step || 100)).toFixed(6))))}><ArrowUp size={14}/>拉长 {step || 100} mm</button>
          <button type="button" className="ghost" disabled={busy || !resize.enabled || current === undefined || (valid ? target : current) <= (step || 100)} onClick={() => changeHeight(String(Number(((valid ? target : current!) - (step || 100)).toFixed(6))))}><ArrowDown size={14}/>缩短 {step || 100} mm</button>
        </div>
        <p className="hint">保持当前 X、Y 尺寸，只调整高度。快捷步长取第一套规则的横撑分格；多套规则仍以真实 CAD 预览为准。</p>
        {current !== undefined && valid && <p className="brace-height-delta">{Math.abs(target - current) < .001 ? "目标高度与当前相同" : `${target > current ? "拉长" : "缩短"} ${Math.abs(target - current).toFixed(2)} mm`}</p>}
      </section>
      {(resize.job || resize.error) && <section className="brace-preview-status" aria-live="polite">
        {resize.job && <strong>{resize.job.message}</strong>}
        {running && <><p>正在计算预览，尚未保存。可继续修改高度或补齐选择，修改后会停止本次计算。</p><button type="button" className="ghost" onClick={resize.cancel}>停止计算</button></>}
        {resize.error && <p role="alert">{resize.error}</p>}
        {resize.job?.result?.document && <><p>结果高度：{resize.job.result.dimensions[2].toFixed(2)} mm</p><div className="brace-counts"><span>新增 <b>{resize.job.result.structureChanges?.added.length || 0}</b> 件</span><span>移除 <b>{resize.job.result.structureChanges?.removed.length || 0}</b> 件</span><span>调整 <b>{resize.job.result.changes?.length || 0}</b> 件</span></div></>}
      </section>}
      <details className="brace-rule-details"><summary>已配置规则（{rules.length}）</summary>
        {rules.length === 0 ? <p>制作完成的柱片模板会自带规则，使用时无需配置。</p> : rules.map(rule => <article key={rule.id}><h4>{rule.label}</h4><p>立柱：{member(rule.leftColumnMemberId)} / {member(rule.rightColumnMemberId)}</p><p>横撑：{member(rule.horizontalMemberId)}；斜撑：{member(rule.diagonalMemberId)}</p><p>横撑分格：{(parameter(rule.pitchParameterId)?.value || 0) * (rule.columnInterfaceIndexStep ?? 1)} mm · {rule.remainderPolicy === "top-clearance" ? "不足一格留在顶部" : "要求完整分格"}</p><p>底部余量 {parameter(rule.bottomOffsetParameterId)?.value ?? "—"} mm / 顶部余量 {parameter(rule.topClearanceParameterId)?.value ?? "—"} mm</p></article>)}
      </details>
    </div>
    <footer><p>预览不会保存模型；点击应用后整体保存高度与补齐选择。</p><div><button type="button" className="ghost" disabled={!resize.enabled || busy || running || !valid || current === undefined || needsRules} onClick={() => resize.previewHeight(target)}>{running ? "正在计算预览…" : "预览高度调整"}</button><button type="button" className="primary" disabled={!resize.enabled || resize.applying || !resize.preview} onClick={() => void resize.apply()}>{resize.applying ? "正在保存…" : "应用"}</button><button type="button" className="ghost" disabled={resize.applying} onClick={close}>取消</button></div></footer>
  </dialog>;
}
