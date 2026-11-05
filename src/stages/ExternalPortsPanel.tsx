import { useState } from "react";
import { AlertTriangle, Crosshair, Plus, Trash2 } from "lucide-react";
import type { ComponentDocument, ComponentPort, InterfaceSource, PortTrialPreview } from "../domain/component";
import type { DraftCommand, DraftSummary, PortDiagnostic } from "../app/sharedDraft";
import { PORT_RELATIONS, resolvePort } from "../domain/ports";
import { emptySpecification, PortSpecificationEditor } from "./PortSpecificationEditor";
import { PortBasisPanel } from "./PortBasisPanel";
import { PortTrialPanel } from "./PortTrialPanel";

type Props = {
  document: ComponentDocument; draftId: string; diagnostics?: PortDiagnostic[]; targets: DraftSummary[];
  onExecute: (action: DraftCommand, revision?: number, reason?: string) => Promise<unknown>;
  onLocate: (source: InterfaceSource) => void;
  onTrialPreview: (preview: PortTrialPreview | null) => void;
};
const typeNames: Record<string, string> = { plane: "面接口", axis: "轴接口", point: "点接口" };
const roleNames = { male: "插入侧", female: "容纳侧", symmetric: "对称配合", unknown: "角色未确定" };
const contactNames = { clearance: "间隙配合", contact: "接触配合", "press-fit": "压配合", unknown: "接触方式未确定" };
const tags = (text: string) => [...new Set(text.split(/[,，\n]/).map(item => item.trim()).filter(Boolean))];
const vectorText = (values: number[], degrees = false) => values.map(value => (value * (degrees ? 180 / Math.PI : 1)).toFixed(3)).join(" / ");

function PortEditor({ document, port, onExecute, onClose }: Pick<Props, "document" | "onExecute"> & { port: ComponentPort | null; onClose: () => void }) {
  const options = document.assembly.members.flatMap(member => member.interfaces.filter(source => !document.ports.some(item => item.id !== port?.id && item.source.memberId === member.id && item.source.interfaceId === source.id)).map(source => ({ member, source, value: JSON.stringify({ memberId: member.id, interfaceId: source.id }) })));
  const [sourceValue, setSourceValue] = useState(port ? JSON.stringify(port.source) : options[0]?.value || "");
  const [name, setName] = useState(port?.name || (options[0] ? `${options[0].member.role}-${options[0].source.name}` : ""));
  const [ownTags, setOwnTags] = useState((port?.tags || options[0]?.source.tags || []).join(", "));
  const [compatibleTags, setCompatibleTags] = useState(port?.compatibleTags?.join(", ") || "");
  const [required, setRequired] = useState(port?.required || false);
  const [relations, setRelations] = useState<ComponentPort["allowedRelations"]>(port?.allowedRelations || []);
  const [specification, setSpecification] = useState(structuredClone(port?.specification || emptySpecification()));
  const [baseRevision] = useState(document.revision);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const selected = options.find(item => item.value === sourceValue);
  return <form className="port-editor" aria-label={port ? "编辑对外接口" : "新增对外接口"} onSubmit={async event => {
    event.preventDefault();
    if (!selected || !name.trim() || saving) return;
    const normalized = { ...specification, category: specification.category.trim(), dimensions: specification.dimensions.map(item => ({ ...item, key: item.key.trim() })) };
    if (normalized.dimensions.some(item => !item.key) || new Set(normalized.dimensions.map(item => item.key)).size !== normalized.dimensions.length) { setError("关键尺寸标识不能为空或重复。"); return; }
    if (normalized.clearanceMinMm !== null && normalized.clearanceMaxMm !== null && normalized.clearanceMinMm > normalized.clearanceMaxMm) { setError("间隙下限不能大于上限。"); return; }
    setSaving(true); setError("");
    try {
      const fields = { name: name.trim(), source: { memberId: selected.member.id, interfaceId: selected.source.id }, tags: tags(ownTags), compatibleTags: tags(compatibleTags), required, allowedRelations: relations || [], specification: normalized };
      const result = await onExecute(port ? { type: "port.patch", id: port.id, patch: fields } : { type: "port.add", port: { id: crypto.randomUUID(), ...fields } }, baseRevision, port ? "人工编辑对外连接规则与尺寸规格" : "从已有成员接口暴露组件对外契约");
      if (result) onClose(); else setError("保存未完成，请查看操作与诊断。");
    } finally { setSaving(false); }
  }}>
    <h4>{port ? "编辑对外契约" : "从成员接口暴露"}</h4>
    {document.revision !== baseRevision && <p className="port-warning" role="status">草稿已更新。请先取消并重新打开，避免覆盖其他修改。</p>}
    <label>对外名称<input value={name} required maxLength={200} placeholder="例如：左侧立柱连接口" onChange={event => setName(event.target.value)}/></label>
    <label>来源接口<select value={sourceValue} required onChange={event => setSourceValue(event.target.value)}>
      {!selected && <option value={sourceValue}>请选择有效成员接口</option>}
      {options.map(item => <option key={item.value} value={item.value}>{item.member.role} / {item.source.name} · {typeNames[item.source.type]} · {item.source.frame ? "已有坐标，待核对" : "待补坐标"}</option>)}
    </select></label>
    {!options.length && <p className="hint">暂无可暴露接口：请先添加成员，或编辑已暴露接口。</p>}
    <label>本接口标签<input value={ownTags} placeholder="用逗号分隔，例如 hook, left" onChange={event => setOwnTags(event.target.value)}/></label>
    <label>对方必须具备的全部标签<input value={compatibleTags} placeholder="例如 column-slot" onChange={event => setCompatibleTags(event.target.value)}/></label>
    <fieldset className="port-relations"><legend>允许的连接方式</legend>{PORT_RELATIONS.map(relation => <label key={relation}><input type="checkbox" checked={relations?.includes(relation) || false} onChange={event => setRelations(event.target.checked ? [...relations || [], relation] : relations?.filter(item => item !== relation))}/>{relation}</label>)}</fieldset>
    <PortSpecificationEditor value={specification} onChange={setSpecification}/>
    <label className="port-checkbox"><input type="checkbox" checked={required} onChange={event => setRequired(event.target.checked)}/>上层装配必须连接此接口</label>
    <p className="hint">允许先保存未知数据。数据填写完整后仍需确认基准、选择目标试配；当前刚体试配支持固定连接。</p>
    {error && <p className="port-warning" role="alert">{error}</p>}
    <div className="action-row"><button type="submit" className="primary" disabled={!selected || !name.trim() || saving || document.revision !== baseRevision}>{saving ? "保存中…" : "保存对外接口"}</button><button type="button" className="ghost" onClick={onClose}>取消</button></div>
  </form>;
}

export function ExternalPortsPanel(props: Props) {
  const { document, onExecute, onLocate } = props;
  const [editing, setEditing] = useState<ComponentPort | null | undefined>(undefined);
  const [removeId, setRemoveId] = useState<string | null>(null);
  const [basisId, setBasisId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const { ports, assembly } = document;
  const diagnostics: PortDiagnostic[] = ports.map(port => props.diagnostics?.find(item => item.portId === port.id) || resolvePort(document, port));
  const shown = ports.filter(port => `${port.name} ${port.id} ${port.specification?.category || ""}`.toLowerCase().includes(search.toLowerCase()));
  const changedCount = diagnostics.filter(item => item.reviewReasons?.length).length;
  return <div className="external-ports">
    <div className="callout">第二步负责组件内部装配；第三步定义整个组件的对外连接契约，并独立检查、试配与复核。</div>
    <div className="port-heading"><h4>对外接口 · {ports.length}</h4><button className="ghost" disabled={editing !== undefined} onClick={() => setEditing(null)}><Plus size={13}/>暴露接口</button></div>
    <div className="port-overview" role="status"><span>定义完整 <b>{diagnostics.filter(item => item.layers?.definition === "pass").length}</b></span><span>基准确认 <b>{diagnostics.filter(item => item.layers?.basis === "pass").length}</b></span><span>当前试配 <b>{diagnostics.filter(item => item.trialCurrent).length}</b></span><span>人工复核 <b>{diagnostics.filter(item => item.layers?.review === "pass").length}</b></span></div>
    <p className="hint">按“定义 → 基准 → 双边规则 → 几何 → 人工复核”逐项核对。几何对齐与精确碰撞分别报告。</p>
    {changedCount > 0 && <p className="port-warning" role="status"><AlertTriangle size={13}/> {changedCount} 个接口的依据发生变化，请查看卡片中的重检原因。</p>}
    {editing !== undefined && <PortEditor document={document} port={editing} onExecute={onExecute} onClose={() => setEditing(undefined)}/>}
    {ports.length > 0 && <label>查找接口<input type="search" value={search} placeholder="名称、接口 ID 或规格族" onChange={event => setSearch(event.target.value)}/></label>}
    {!ports.length && <p className="empty-text">点击“暴露接口”，从成员已有接口开始。没有图纸的尺寸、公差可先留为未确定。</p>}
    {assembly.members.length > 0 && assembly.members.every(member => member.interfaces.length === 0) && <div className="port-warning" role="status">当前 {assembly.members.length} 个成员均未提供来源接口，暂时无法暴露对外连接口。请先在来源零部件平台定义接口并发布，再在本组件中使用该发布版本；这里不会自动猜测连接点或朝向。</div>}
    {shown.map(port => {
      const result = diagnostics.find(item => item.portId === port.id)!;
      const member = assembly.members.find(item => item.id === port.source.memberId);
      const source = member?.interfaces.find(item => item.id === port.source.interfaceId);
      const spec = port.specification;
      return <article className="port-card" key={port.id} aria-label={`对外接口 ${port.name}`}>
        <div className="port-heading"><strong>{port.name || "未命名接口"}</strong><span className={`port-state ${result.layers?.review === "pass" ? "ready" : "needs-review"}`}>{result.layers?.review === "pass" ? "人工已复核" : result.reviewReasons?.length ? "需重检" : "待验证"}</span></div>
        <div className="port-layer-grid" aria-label="接口验证分层">{[["定义", result.layers?.definition], ["基准", result.layers?.basis], ["规则", result.layers?.rules], ["几何", result.layers?.geometry], ["人工", result.layers?.review]].map(([label, state]) => <span className={`port-layer ${state === "pass" ? "pass" : state === "stale" || state === "fail" ? "warn" : "pending"}`} key={label}>{label}<small>{state === "pass" ? "通过" : state === "alignment-pass-collision-pending" ? "对齐✓ 碰撞待检" : state === "stale" ? "需重检" : state === "fail" ? "未通过" : "待检查"}</small></span>)}</div>
        <p className="port-source-label">{member?.role || "成员失效"} / {source?.name || "接口失效"} · {result.type ? typeNames[result.type] : "类型未知"} · {port.required ? "必须连接" : "可选连接"}</p>
        <dl><dt>规格族 / 配合</dt><dd>{spec?.category || "未确定"} · {spec ? roleNames[spec.role] : "角色未确定"} · {spec ? contactNames[spec.contact] : "接触未确定"}</dd><dt>关键尺寸</dt><dd>{spec?.dimensions.map(item => `${item.key}: ${item.value ?? "?"} ± ${item.tolerance ?? "?"} ${item.unit}`).join(" / ") || "未确定"}</dd><dt>允许间隙 · mm</dt><dd>{spec?.clearanceMinMm ?? "?"} ～ {spec?.clearanceMaxMm ?? "?"}</dd><dt>组件连接点 XYZ · mm</dt><dd>{result.frame ? vectorText(result.frame.origin) : "缺少来源坐标"}</dd><dt>方向 XYZ · °</dt><dd>{result.frame ? vectorText(result.frame.rotation, true) : "未确定"}</dd></dl>
        {(result.issues.length > 0 || (result.specificationIssues?.length || 0) > 0) && <details className="port-problems"><summary>待补充事项 · {result.issues.length + (result.specificationIssues?.length || 0)}</summary><ul className="port-issues">{result.issues.map(issue => <li key={issue.code}>{issue.message}<code>{issue.code}</code></li>)}{result.specificationIssues?.map(message => <li key={message}>{message}<code>SPECIFICATION_INCOMPLETE</code></li>)}</ul></details>}
        {result.reviewReasons?.map(message => <p className="port-warning" key={message}>{message}</p>)}
        <div className="action-row"><button className="ghost" disabled={!source} onClick={() => onLocate(port.source)}><Crosshair size={13}/>定位</button><button className="ghost" onClick={() => { setBasisId(basisId === port.id ? null : port.id); onLocate(port.source); }}>基准确认</button><button className="ghost" disabled={editing !== undefined} onClick={() => setEditing(structuredClone(port))}>编辑规则</button><button className="ghost" aria-label={`取消暴露 ${port.name}`} onClick={() => setRemoveId(port.id)}><Trash2 size={13}/></button></div>
        {basisId === port.id && <PortBasisPanel key={`${port.id}-${document.revision}`} document={document} port={port} diagnostic={result} onExecute={onExecute} onLocate={() => onLocate(port.source)}/>}
        <PortTrialPanel draftId={props.draftId} document={document} port={port} diagnostic={result} targets={props.targets} onPreview={props.onTrialPreview}
          onRecord={async (options, revision) => !!await onExecute({ type: "port.trial.record", id: port.id, options }, revision, "按双方当前版本重算并保存接口试配报告，正式装配位置保持原样")}
          onReview={async (note, revision) => !!await onExecute({ type: "port.confirm", id: port.id, scope: "review", note }, revision, note)}/>
        <details className="port-source-data"><summary>来源版本、稳定 ID 与连接契约</summary><dl><dt>稳定 ID</dt><dd><code>{port.id}</code></dd><dt>来源引用</dt><dd>{port.source.memberId} / {port.source.interfaceId}</dd><dt>固定发布版本</dt><dd>V{result.sourceVersion?.version ?? "?"} · {result.sourceVersion?.publicationId}<small>SHA256 {result.sourceVersion?.sha256}</small></dd><dt>本侧标签 / 对方必需标签</dt><dd>{port.tags.join(", ") || "未定义"} / {port.compatibleTags?.join(", ") || "未定义"}</dd><dt>允许的连接方式</dt><dd>{port.allowedRelations?.join(" / ") || "未定义"}</dd></dl><pre>{JSON.stringify({ diagnostic: result, basisConfirmation: port.basisConfirmation, reviewConfirmation: port.reviewConfirmation }, null, 2)}</pre></details>
        {removeId === port.id && <div className="port-remove-confirm"><p>取消此对外接口？来源零件和内部连接会保留，可重新暴露。</p><div className="action-row"><button className="ghost" onClick={async () => { if (await onExecute({ type: "port.remove", id: port.id }, document.revision, "人工取消对外暴露")) setRemoveId(null); }}>确认取消暴露</button><button className="ghost" onClick={() => setRemoveId(null)}>保留</button></div></div>}
      </article>;
    })}
    {ports.length > 0 && !shown.length && <p className="empty-text">没有匹配的接口。</p>}
  </div>;
}
