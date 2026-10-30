import { CheckCircle2, CircleHelp, Plus, Trash2 } from "lucide-react";
import type { ComponentMember, ComponentPort, ComponentSlot, InterfaceSelection, PartInterface, ResolvedPartInterface } from "../domain/component";
import { sameInterfaceSelection, supportsInterfacePreview } from "../domain/interfaceGeometry";
import { interfacesCompatible } from "../domain/interfaceCompatibility";

function interfaceSummary(item: PartInterface): string {
  const size = item.interfaceSize;
  const sizeText = size?.diameter ? `Ø${size.diameter} ${size.unit || "mm"}`
    : size && [size.width, size.height, size.depth].some(value => value !== undefined)
      ? `${[size.width, size.height, size.depth].filter(value => value !== undefined).join(" × ")} ${size.unit || "mm"}`
      : undefined;
  const region = item.region?.uSpan !== undefined && item.region.vSpan !== undefined ? `区域 ${item.region.uSpan} × ${item.region.vSpan} mm` : undefined;
  return [item.type, item.interfaceType, item.locatingType, item.connectionMethod, sizeText, region].filter(Boolean).join(" / ") || "未声明类型";
}

function PortNameEditor({ port, onRename }: { port: ComponentPort; onRename: (id: string, name: string) => void }) {
  return <input className="port-name-input" key={`${port.id}:${port.name}`} defaultValue={port.name} aria-label={`编辑对外接口名称 ${port.name}`}
    onBlur={event => {
      const name = event.currentTarget.value.trim();
      if (!name) { event.currentTarget.value = port.name; return; }
      if (name !== port.name) onRename(port.id, name);
    }}
    onKeyDown={event => {
      if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
      else if (event.key === "Escape") { event.currentTarget.value = port.name; event.currentTarget.blur(); }
    }}/>;
}

function sourceKey(source: { memberId: string; interfaceId: string } | null | undefined): string {
  return source ? `${encodeURIComponent(source.memberId)}:${encodeURIComponent(source.interfaceId)}` : "";
}

function parseSource(value: string): { memberId: string; interfaceId: string } | null {
  const separator = value.indexOf(":");
  if (separator < 1 || separator === value.length - 1) return null;
  try {
    const memberId = decodeURIComponent(value.slice(0, separator));
    const interfaceId = decodeURIComponent(value.slice(separator + 1));
    return memberId && interfaceId ? { memberId, interfaceId } : null;
  } catch {
    return null;
  }
}

function sourceLabel(members: ComponentMember[], source: { memberId: string; interfaceId: string } | null | undefined): string {
  if (!source) return "未配置";
  const member = members.find(item => item.id === source.memberId);
  const item = member?.interfaces.find(interfaceItem => interfaceItem.id === source.interfaceId);
  return `${member?.name || source.memberId} · ${item?.name || source.interfaceId}`;
}

type SlotPatch = { name?: string; required?: boolean; baseInterface?: { memberId: string; interfaceId: string } | null; memberId?: string | null; interfaceId?: string | null };

export function InterfacesStage({ members, ports, slots, selectedInterface, interfacePreview, editable, onSelectInterface, onSetInterfaceExposure, onRenamePort, onAddSlot, onPatchSlot, onRemoveSlot }: { members: ComponentMember[]; ports: ComponentPort[]; slots: ComponentSlot[]; selectedInterface: InterfaceSelection | null; interfacePreview?: { selection: InterfaceSelection; status: "loading" | "ready" | "error"; resolvedInterfaces?: ResolvedPartInterface[] }; editable: boolean; onSelectInterface: (selection: InterfaceSelection) => void; onSetInterfaceExposure: (memberId: string, interfaceId: string, exposed: boolean) => void; onRenamePort: (id: string, name: string) => void; onAddSlot: () => void; onPatchSlot: (id: string, patch: SlotPatch) => void; onRemoveSlot: (id: string) => void }) {
  return <>
    <div className="callout">组件接口保留其来源成员和零部件接口引用，发布版本保持固定。</div>
    <h4>对外接口</h4>
    {ports.map(port => {
      const sourceMember = members.find(member => member.id === port.source.memberId);
      const sourceInterface = sourceMember?.interfaces.find(item => item.id === port.source.interfaceId);
      return <div className="info-row port-row" key={port.id}><CheckCircle2 size={14}/><span className="port-info">
        <PortNameEditor port={port} onRename={onRenamePort}/>
        <small>来源：{sourceMember?.name || port.source.memberId} / {sourceInterface?.name || port.source.interfaceId}</small>
        <small>ID：{port.source.memberId} / {port.source.interfaceId}</small><small>{port.tags.join(", ")}</small>
        {port.interface && <small>{[port.interface.interfaceType || port.interface.type, port.interface.locatingType, port.interface.region?.mode, port.interface.region?.uSpan !== undefined && port.interface.region?.vSpan !== undefined ? `${port.interface.region.uSpan} × ${port.interface.region.vSpan} mm` : undefined, port.interface.region?.uSpanExpression || port.interface.region?.vSpanExpression ? `U ${port.interface.region?.uSpanExpression || "?"} × V ${port.interface.region?.vSpanExpression || "?"}` : undefined, port.interface.region?.countExpression ? `数量表达式 ${port.interface.region.countExpression}` : undefined].filter(Boolean).join(" / ")}</small>}
      </span></div>;
    })}
    {ports.length === 0 && <p className="empty-text">在下方零部件接口清单中勾选“对外开放”</p>}
    <h4>零部件接口</h4>
    <div className="member-interface-list">
      {members.map(member => <section className="member-interface-group" key={member.id} aria-label={`${member.name}接口`}>
        <div className="member-interface-heading"><b>{member.role || member.name}</b><small>{member.interfaces.length} 个 · {member.code}</small></div>
        {member.interfaces.length === 0 && <p className="empty-text">该零部件未定义接口</p>}
        {member.interfaces.map(item => {
          const selected = sameInterfaceSelection(selectedInterface, { memberId: member.id, interfaceId: item.id });
          const activePreview = selected && sameInterfaceSelection(interfacePreview?.selection || null, selectedInterface) ? interfacePreview : undefined;
          const resolvedInterfaces = activePreview?.status === "ready" ? activePreview.resolvedInterfaces : member.resolvedInterfaces;
          const previewable = supportsInterfacePreview(item, resolvedInterfaces);
          const occurrence = member.resolvedInterfaces?.find(entry => entry.id === item.id);
          const sourceId = occurrence?.sourceInterfaceId || item.id;
          const sourceFeature = occurrence?.sourceFeatureId || occurrence?.sourceFeatureRuleId;
          const externalPort = ports.find(port => port.source.memberId === member.id && port.source.interfaceId === item.id);
          return <div className={`interface-select ${selected ? "selected" : ""} ${externalPort ? "exposed" : ""}`} key={`${member.id}:${item.id}`}>
            <label className="interface-exposure"><input type="checkbox" checked={Boolean(externalPort)} aria-label={`将 ${item.name} 设置为对外接口`} onChange={event => onSetInterfaceExposure(member.id, item.id, event.currentTarget.checked)}/><span>对外开放</span></label>
            <button type="button" className="interface-preview-button" aria-pressed={selected} onClick={() => onSelectInterface({ memberId: member.id, interfaceId: item.id })}>
              <span className="interface-select-name"><b>{item.name}</b><small>ID：{item.id}</small><small>{interfaceSummary(item)}</small><small>来源 ID：{sourceId}{sourceFeature ? ` · 特征：${sourceFeature}` : ""}</small>{item.geometryRefs?.length ? <small>语义面：{item.geometryRefs.join("、")}</small> : null}</span>
              {previewable ? <CheckCircle2 size={14} aria-label="可按语义面预览"/> : <CircleHelp size={14} aria-label="缺少可定位几何数据"/>}
            </button>
          </div>;
        })}
      </section>)}
      {members.length === 0 && <p className="empty-text">添加零部件后可查看已定义接口</p>}
    </div>
    {selectedInterface && (() => {
      const member = members.find(item => item.id === selectedInterface.memberId);
      const item = member?.interfaces.find(interfaceItem => interfaceItem.id === selectedInterface.interfaceId);
      if (!member || !item) return null;
      return <p className="interface-preview-note" role="status">
        {sameInterfaceSelection(interfacePreview?.selection || null, selectedInterface) && interfacePreview?.status === "loading"
          ? "正在按该零部件当前参数解析接口的具体位置…"
          : supportsInterfacePreview(item, interfacePreview?.status === "ready" ? interfacePreview.resolvedInterfaces : member.resolvedInterfaces)
            ? "已定位接口的具体区域，中央三维视图已同步高亮。"
            : "接口已定义，但当前缺少可定位几何数据；中央三维视图仅保留零部件模型。"}
      </p>;
    })()}
    <div className="section-heading"><h4>可替换槽位</h4><button type="button" className="ghost compact-action" onClick={onAddSlot} disabled={!editable || members.every(member => member.interfaces.length === 0)}><Plus size={13}/>新增槽位</button></div>
    {!editable && <p className="slot-status warning" role="status">共享草稿当前不可编辑。请先在右侧处理加载或同步状态，然后再配置槽位。</p>}
    {slots.map(slot => {
      const baseInterface = slot.baseInterface ?? null;
      const basePart = baseInterface ? members.find(member => member.id === baseInterface.memberId)?.interfaces.find(item => item.id === baseInterface.interfaceId) : undefined;
      const assigned = slot.memberId && slot.interfaceId ? { memberId: slot.memberId, interfaceId: slot.interfaceId } : null;
      const evaluated = basePart ? members.flatMap(member => member.interfaces.map(item => {
        const result = interfacesCompatible(basePart, item);
        const occupied = slots.find(other => other.id !== slot.id && other.memberId === member.id && other.interfaceId === item.id);
        return { member, item, result, occupied };
      })).filter(candidate => candidate.member.id !== baseInterface?.memberId) : [];
      const candidates = evaluated.filter(candidate => candidate.result.compatible && !candidate.occupied);
      const unavailable = evaluated.filter(candidate => !candidate.result.compatible || candidate.occupied);
      const assignmentValue = sourceKey(assigned);
      const unavailableMessage = evaluated.length === 0
        ? "没有其他成员接口可供此槽位占用。"
        : candidates.length === 0 && unavailable.every(candidate => candidate.occupied)
          ? "符合条件的接口已被其他槽位占用。"
          : candidates.length === 0 && unavailable.every(candidate => candidate.occupied || candidate.result.reason === "接口尺寸缺失")
            ? "同几何类型接口存在，但尺寸信息缺失，当前无法确认兼容性。"
            : candidates.length === 0 && unavailable.every(candidate => candidate.occupied || candidate.result.reason === "接口几何类型不匹配")
              ? "其他成员没有相同几何类型的接口；请选择合适的基准接口。"
              : "暂无兼容接口；请展开下方列表查看具体不兼容原因。";
      return <div className={`slot-editor ${!baseInterface || (slot.required && !assigned) ? "slot-warning" : ""}`} key={slot.id}>
        <div className="slot-editor-header"><input className="slot-name-input" defaultValue={slot.name} disabled={!editable} aria-label={`编辑槽位名称 ${slot.name}`} onBlur={event => { const name = event.currentTarget.value.trim(); if (name && name !== slot.name) onPatchSlot(slot.id, { name }); else event.currentTarget.value = slot.name; }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }}/><button type="button" className="icon-button" aria-label={`删除槽位 ${slot.name}`} title="删除槽位" disabled={!editable} onClick={() => onRemoveSlot(slot.id)}><Trash2 size={14}/></button></div>
        <label className="slot-required"><input type="checkbox" checked={slot.required} disabled={!editable} aria-label={`${slot.name}：必填槽位`} onChange={event => onPatchSlot(slot.id, { required: event.currentTarget.checked })}/>必填槽位</label>
        <label>基准接口<select aria-label={`${slot.name}：基准接口`} disabled={!editable} value={sourceKey(baseInterface)} onChange={event => onPatchSlot(slot.id, { baseInterface: parseSource(event.currentTarget.value) })}>
          <option value="">待配置基准接口</option>
          {members.flatMap(member => member.interfaces.map(item => <option key={`${member.id}:${item.id}`} value={sourceKey({ memberId: member.id, interfaceId: item.id })}>{member.name} · {item.name}</option>))}
        </select></label>
        {baseInterface && <button type="button" className="slot-preview-link" onClick={() => onSelectInterface(baseInterface)}>预览基准接口 · {sourceLabel(members, baseInterface)}</button>}
        <label>占用接口<select aria-label={`${slot.name}：占用接口`} value={assignmentValue} disabled={!editable || !basePart || candidates.length === 0} onChange={event => { const next = parseSource(event.currentTarget.value); onPatchSlot(slot.id, next ? { memberId: next.memberId, interfaceId: next.interfaceId } : { memberId: null, interfaceId: null }); }}>
          <option value="">{basePart ? "未分配占用接口" : "请先选择基准接口"}</option>
          {candidates.map(candidate => <option key={`${candidate.member.id}:${candidate.item.id}`} value={sourceKey({ memberId: candidate.member.id, interfaceId: candidate.item.id })}>{candidate.member.name} · {candidate.item.name}{candidate.result.reason === "几何类型匹配；尺寸未声明" ? " · 尺寸未声明" : ""}</option>)}
        </select></label>
        {assigned && <button type="button" className="slot-preview-link" onClick={() => onSelectInterface(assigned)}>预览占用接口 · {sourceLabel(members, assigned)}</button>}
        {!baseInterface && <small className="slot-status warning">请先选择基准接口</small>}
        {baseInterface && !assigned && slot.required && <small className="slot-status warning">必填槽位尚未分配兼容接口</small>}
        {basePart && !candidates.length && <small className="slot-status warning">{unavailableMessage}</small>}
        {unavailable.length > 0 && <details className="slot-unavailable"><summary>查看不可用接口及原因（{unavailable.length}）</summary>{unavailable.map(candidate => <small key={`${candidate.member.id}:${candidate.item.id}`}>{candidate.member.name} · {candidate.item.name}：{candidate.occupied ? `已被槽位“${candidate.occupied.name}”占用` : candidate.result.reason}</small>)}</details>}
        {assigned && <small className="slot-status">已分配：{sourceLabel(members, assigned)}</small>}
      </div>;
    })}
    {slots.length === 0 && <p className="empty-text">暂无槽位，点击“新增槽位”开始配置</p>}
  </>;
}
