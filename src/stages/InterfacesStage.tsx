import { CheckCircle2, CircleHelp } from "lucide-react";
import type { ComponentMember, ComponentPort, ComponentSlot, InterfaceSelection, PartInterface, ResolvedPartInterface } from "../domain/component";
import { sameInterfaceSelection, supportsInterfacePreview } from "../domain/interfaceGeometry";

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

export function InterfacesStage({ members, ports, slots, selectedInterface, interfacePreview, onSelectInterface, onSetInterfaceExposure, onRenamePort }: { members: ComponentMember[]; ports: ComponentPort[]; slots: ComponentSlot[]; selectedInterface: InterfaceSelection | null; interfacePreview?: { selection: InterfaceSelection; status: "loading" | "ready" | "error"; resolvedInterfaces?: ResolvedPartInterface[] }; onSelectInterface: (selection: InterfaceSelection) => void; onSetInterfaceExposure: (memberId: string, interfaceId: string, exposed: boolean) => void; onRenamePort: (id: string, name: string) => void }) {
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
    <h4>可替换槽位</h4>
    {slots.map(slot => <div className="info-row" key={slot.id}><span>{slot.name}<small>{slot.required ? "必填" : "可选"} · {slot.memberId || "未放置成员"}</small></span></div>)}
    {slots.length === 0 && <p className="empty-text">暂无槽位，槽位编辑尚未接入</p>}
  </>;
}
