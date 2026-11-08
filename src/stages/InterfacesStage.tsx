import { CheckCircle2, CircleHelp, Link2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { ComponentMember, ComponentPort, ComponentSlot, InterfaceSelection, Mate, PartInterface, ResolvedPartInterface } from "../domain/component";
import { sameInterfaceSelection, supportsInterfacePreview } from "../domain/interfaceGeometry";
import { interfacesCompatible } from "../domain/interfaceCompatibility";
import { assessInterfaceCompatibility } from "../domain/interfaceCompatibility";
import { interfaceTypeLabel, PRESET_INTERFACE_TYPES } from "../domain/interfaceTypes";
import { interfaceContractGroups } from "../domain/interfaceContractGroups";

function interfaceSummary(item: PartInterface, members: ComponentMember[], customTypes: { id: string; label: string }[]): string {
  const size = item.interfaceSize;
  const sizeText = size?.diameter ? `Ø${size.diameter} ${size.unit || "mm"}`
    : size && [size.width, size.height, size.depth].some(value => value !== undefined)
      ? `${[size.width, size.height, size.depth].filter(value => value !== undefined).join(" × ")} ${size.unit || "mm"}`
      : undefined;
  const region = item.region?.uSpan !== undefined && item.region.vSpan !== undefined ? `区域 ${item.region.uSpan} × ${item.region.vSpan} mm` : undefined;
  return [interfaceTypeLabel(item.connectionTypeId, members.flatMap(member => member.interfaces), customTypes), item.type, item.interfaceType, item.locatingType, item.connectionMethod, sizeText, region].filter(Boolean).join(" / ") || "未声明类型";
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

export function InterfacesStage({ members, ports, slots, mates, interfaceTypes, selectedInterface, interfacePreview, editable, onSelectInterface, onConnect, onRemoveConnection, onSetInterfaceExposure, onSetInterfaceType, onRenamePort, onAddSlot, onPatchSlot, onRemoveSlot }: { members: ComponentMember[]; ports: ComponentPort[]; slots: ComponentSlot[]; mates: Mate[]; interfaceTypes: { id: string; label: string }[]; selectedInterface: InterfaceSelection | null; interfacePreview?: { selection: InterfaceSelection; status: "loading" | "ready" | "error"; resolvedInterfaces?: ResolvedPartInterface[] }; editable: boolean; onSelectInterface: (selection: InterfaceSelection) => void; onConnect: (from: InterfaceSelection, to: InterfaceSelection) => Promise<boolean>; onRemoveConnection: (id: string) => void; onSetInterfaceExposure: (memberId: string, interfaceId: string, exposed: boolean) => void; onSetInterfaceType: (memberId: string, interfaceId: string, connectionTypeId: string | null, customTypeLabel?: string) => void; onRenamePort: (id: string, name: string) => void; onAddSlot: () => void; onPatchSlot: (id: string, patch: SlotPatch) => void; onRemoveSlot: (id: string) => void }) {
  const [customTypeLabel, setCustomTypeLabel] = useState("");
  const [connectionFrom, setConnectionFrom] = useState<InterfaceSelection | null>(null);
  const [connectionTo, setConnectionTo] = useState<InterfaceSelection | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectionNotice, setConnectionNotice] = useState("");
  const [failedConnections, setFailedConnections] = useState<Array<{ id: string; from: InterfaceSelection; to: InterfaceSelection; reason: string; checks: Array<{ code: string; status: string; message: string }> }>>([]);
  const isOccupied = (selection: InterfaceSelection) => mates.some(mate =>
    sameInterfaceSelection(mate.from, selection) || sameInterfaceSelection(mate.to, selection));
  const selectionLabel = (selection: InterfaceSelection | null) => selection ? sourceLabel(members, selection) : "点击下方接口选择";
  const resolveEndpoint = (selection: InterfaceSelection | null) => {
    const member = selection ? members.find(item => item.id === selection.memberId) : undefined;
    return selection && member ? { member, item: member.interfaces.find(item => item.id === selection.interfaceId) } : undefined;
  };
  const pendingFrom = resolveEndpoint(connectionFrom);
  const pendingTo = resolveEndpoint(connectionTo);
  const pendingAssessment = pendingFrom?.item && pendingTo?.item ? assessInterfaceCompatibility(pendingFrom.item, pendingTo.item, undefined, pendingFrom.member, pendingTo.member) : undefined;
  const pendingFailureReason = connectionFrom && connectionTo
    ? connectionFrom.memberId === connectionTo.memberId
      ? "连接失败：两个接口来自同一个零部件，必须选择不同零部件。"
      : isOccupied(connectionFrom) || isOccupied(connectionTo)
        ? "连接失败：所选接口已经连接过，每个接口只能连接一次。"
        : !pendingFrom?.item || !pendingTo?.item
          ? "连接失败：接口数据不存在或已被更新，请重新选择接口。"
          : pendingAssessment?.reason || "连接失败：缺少接口兼容性检查结果。"
    : undefined;
  const chooseConnectionInterface = (selection: InterfaceSelection) => {
    onSelectInterface(selection);
    if (isOccupied(selection)) return;
    if (connectionTo?.memberId === selection.memberId) {
      setConnectionTo(selection);
      return;
    }
    if (connectionFrom?.memberId === selection.memberId) {
      setConnectionFrom(selection);
      return;
    }
    if (!connectionFrom) { setConnectionFrom(selection); return; }
    if (!connectionTo) { setConnectionTo(selection); return; }
    setConnectionFrom(selection);
    setConnectionTo(null);
  };
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
        {port.interface && <><small>接口类型：{interfaceTypeLabel(port.interface.connectionTypeId, members.flatMap(member => member.interfaces), interfaceTypes)}</small><small>{[port.interface.interfaceType || port.interface.type, port.interface.locatingType, port.interface.region?.mode, port.interface.region?.uSpan !== undefined && port.interface.region?.vSpan !== undefined ? `${port.interface.region.uSpan} × ${port.interface.region.vSpan} mm` : undefined, port.interface.region?.uSpanExpression || port.interface.region?.vSpanExpression ? `U ${port.interface.region?.uSpanExpression || "?"} × V ${port.interface.region?.vSpanExpression || "?"}` : undefined, port.interface.region?.countExpression ? `数量表达式 ${port.interface.region.countExpression}` : undefined].filter(Boolean).join(" / ")}</small></>}
      </span></div>;
    })}
    {ports.length === 0 && <p className="empty-text">在下方零部件接口清单中勾选“对外开放”</p>}
    <h4>连接窗口</h4>
    <div className="interface-connection-window">
      <div className="interface-connection-choice"><b>接口 A</b><span>{selectionLabel(connectionFrom)}</span></div>
      <span className="interface-connection-arrow" aria-hidden="true"><Link2 size={15}/></span>
      <div className="interface-connection-choice"><b>接口 B</b><span>{selectionLabel(connectionTo)}</span></div>
      <button type="button" className="primary full" disabled={connecting || !editable || !connectionFrom || !connectionTo} onClick={async () => {
        if (!connectionFrom || !connectionTo) return;
        if (!pendingAssessment || pendingAssessment.status !== "compatible") {
          setFailedConnections(current => [...current, { id: crypto.randomUUID(), from: connectionFrom, to: connectionTo, reason: pendingFailureReason || "连接失败：未通过接口检查。", checks: pendingAssessment?.checks || [{ code: "CONNECTION_CHECK", status: "fail", message: pendingFailureReason || "未通过接口检查" }] }]);
          setConnectionFrom(null); setConnectionTo(null);
          return;
        }
        setConnecting(true); setConnectionNotice("正在检查最新接口位置并保存连接…");
        try {
          if (await onConnect(connectionFrom, connectionTo)) { setConnectionFrom(null); setConnectionTo(null); setConnectionNotice("连接已保存。"); }
          else setConnectionNotice("连接未完成，请查看工作台状态栏中的具体原因，稍后可重试。");
        } catch (error) {
          setConnectionNotice(`连接失败：${error instanceof Error ? error.message : "请重试"}`);
        } finally { setConnecting(false); }
      }}><Link2 size={15}/>{connecting ? "连接中…" : "连接接口"}</button>
      <small role="status">{!editable ? "草稿暂不可编辑，请先处理草稿同步状态。" : !connectionFrom || !connectionTo ? "请分别点击两个不同零部件的接口，填入接口 A 和接口 B。" : connectionNotice}</small>
      {pendingAssessment && <div className={`interface-check-report ${pendingAssessment.status === "compatible" ? "pass" : "fail"}`} role="status"><b>连接检查：{pendingAssessment.status === "compatible" ? "通过" : pendingAssessment.status === "needs_review" ? "需要复核" : "未通过"}</b><small>{pendingAssessment.reason}</small>{pendingAssessment.checks.map((check, index) => <span key={`${check.code}-${index}`} className={check.status === "pass" ? "check-pass" : "check-fail"}><i>{check.status === "pass" ? "✓" : "×"}</i>{check.message}</span>)}</div>}
    </div>
    <h4>已连接接口</h4>
    <div className="connected-interface-list">
      {mates.length === 0 && <p className="empty-text">暂无连接接口</p>}
      {mates.map(mate => {
        const fromMember = members.find(member => member.id === mate.from.memberId);
        const toMember = members.find(member => member.id === mate.to.memberId);
        const fromInterface = fromMember?.interfaces.find(item => item.id === mate.from.interfaceId);
        const toInterface = toMember?.interfaces.find(item => item.id === mate.to.interfaceId);
        const assessment = fromInterface && toInterface && fromMember && toMember ? assessInterfaceCompatibility(fromInterface, toInterface, mate.relation, fromMember, toMember) : undefined;
        const selected = sameInterfaceSelection(selectedInterface, mate.from) || sameInterfaceSelection(selectedInterface, mate.to);
        return <div className={`connected-interface-row ${selected ? "selected" : ""}`} key={mate.id}>
          <Link2 size={14}/><button type="button" className="connected-interface-preview" aria-pressed={selected} onClick={() => onSelectInterface(mate.from)}>
            <strong>{fromMember?.name || mate.from.memberId} · {fromInterface?.name || mate.from.interfaceId}</strong>
            <b>连接</b>
            <strong>{toMember?.name || mate.to.memberId} · {toInterface?.name || mate.to.interfaceId}</strong>
            <small>{mate.relation} · 偏置 {mate.offset} mm · 已约束移动</small>
            {assessment && <span className="connected-checks"><b>连接检查：{assessment.status === "compatible" ? "通过" : assessment.status === "needs_review" ? "需要复核" : "未通过"}</b>{assessment.checks.map((check, index) => <em key={`${check.code}-${index}`} className={check.status === "pass" ? "check-pass" : "check-fail"}><i>{check.status === "pass" ? "✓" : "×"}</i>{check.message}</em>)}</span>}
          </button>
          <button type="button" className="icon-button" title="删除连接" aria-label={`删除 ${fromInterface?.name || mate.from.interfaceId} 与 ${toInterface?.name || mate.to.interfaceId} 的连接`} disabled={!editable} onClick={() => onRemoveConnection(mate.id)}><Trash2 size={14}/></button>
        </div>;
      })}
      {failedConnections.map(connection => {
        const from = sourceLabel(members, connection.from), to = sourceLabel(members, connection.to);
        return <div className="connected-interface-row connection-failed" key={connection.id}>
          <span className="connection-failed-icon" aria-hidden="true">×</span>
          <span className="connected-interface-preview"><strong>{from}</strong><b>连接失败</b><strong>{to}</strong><small>{connection.reason}</small><span className="connected-checks">{connection.checks.map((check, index) => <em key={`${check.code}-${index}`} className={check.status === "pass" ? "check-pass" : "check-fail"}><i>{check.status === "pass" ? "✓" : "×"}</i>{check.message}</em>)}</span></span>
          <button type="button" className="icon-button" title="删除失败连接" aria-label="删除失败连接" onClick={() => setFailedConnections(current => current.filter(item => item.id !== connection.id))}><Trash2 size={14}/></button>
        </div>;
      })}
    </div>
    <h4>零部件接口</h4>
    <div className="member-interface-list">
      {members.map(member => <section className="member-interface-group" key={member.id} aria-label={`${member.name}接口`}>
        <div className="member-interface-heading"><b>{member.role || member.name}</b><small>{member.interfaces.length} 个 · {member.code}</small></div>
        <small className="interface-resize-hint">拖动右下角可独立调整窗口高度</small>
        {member.interfaces.length === 0 && <p className="empty-text">该零部件未定义接口</p>}
        <div className="member-interface-items">
        {interfaceContractGroups(member.interfaces).map(group => <details className="interface-contract-group" key={group.key} open>
        <summary><strong>{group.label}</strong><span>{group.interfaces.length} 个接口</span></summary>
        {group.interfaces.map(item => {
          const selected = sameInterfaceSelection(selectedInterface, { memberId: member.id, interfaceId: item.id });
          const activePreview = selected && sameInterfaceSelection(interfacePreview?.selection || null, selectedInterface) ? interfacePreview : undefined;
          const resolvedInterfaces = activePreview?.status === "ready" ? activePreview.resolvedInterfaces : member.resolvedInterfaces;
          const previewable = supportsInterfacePreview(item, resolvedInterfaces);
          const externalPort = ports.find(port => port.source.memberId === member.id && port.source.interfaceId === item.id);
          const occupied = isOccupied({ memberId: member.id, interfaceId: item.id });
          const pending = sameInterfaceSelection(connectionFrom, { memberId: member.id, interfaceId: item.id }) || sameInterfaceSelection(connectionTo, { memberId: member.id, interfaceId: item.id });
          return <div className={`interface-select ${selected ? "selected" : ""} ${pending ? "connection-pending" : ""} ${occupied ? "connected" : ""} ${externalPort ? "exposed" : ""}`} key={`${member.id}:${item.id}`}>
            <label className="interface-exposure"><input type="checkbox" checked={Boolean(externalPort)} aria-label={`将 ${item.name} 设置为对外接口`} onChange={event => onSetInterfaceExposure(member.id, item.id, event.currentTarget.checked)}/><span>对外开放</span></label>
            <button type="button" className="interface-preview-button" aria-pressed={selected} onClick={() => chooseConnectionInterface({ memberId: member.id, interfaceId: item.id })}>
              <span className="interface-select-name"><b>{item.name}</b>{occupied && <small className="interface-connected-label">已连接</small>}<small>ID：{item.id}</small>{item.geometryRefs?.length ? <small>语义面：{item.geometryRefs.join("、")}</small> : null}</span>
              {previewable ? <CheckCircle2 size={14} aria-label="可按语义面预览"/> : <CircleHelp size={14} aria-label="缺少可定位几何数据"/>}
            </button>
          </div>;
        })}</details>)}
        </div>
      </section>)}
      {members.length === 0 && <p className="empty-text">添加零部件后可查看已定义接口</p>}
    </div>
    <h4>接口类型定义</h4>
    {(() => {
      const member = selectedInterface ? members.find(item => item.id === selectedInterface.memberId) : undefined;
      const item = member?.interfaces.find(interfaceItem => interfaceItem.id === selectedInterface?.interfaceId);
      if (!member || !item || !selectedInterface) return <p className="empty-text">请先点击上方零部件接口，再设置接口类型。</p>;
      const occurrence = member.resolvedInterfaces?.find(entry => entry.id === item.id);
      const sourceId = occurrence?.sourceInterfaceId || item.id;
      const sourceFeature = occurrence?.sourceFeatureId || occurrence?.sourceFeatureRuleId;
      const customTypes = interfaceTypes.filter(type => type.id.startsWith("custom:"));
      return <div className="interface-type-editor standalone" aria-label="接口类型定义">
        <div className="interface-type-target"><b>{member.name} · {item.name}</b><small>{interfaceSummary(item, members, interfaceTypes)} · 来源 ID：{sourceId}{sourceFeature ? ` · 特征：${sourceFeature}` : ""}</small></div>
        <label>接口类型<select aria-label={`${item.name}：接口类型`} disabled={!editable} value={item.connectionTypeId || ""} onChange={event => onSetInterfaceType(member.id, item.id, event.currentTarget.value || null)}>
          <option value="">未分类</option>
          <optgroup label="预置类型">{PRESET_INTERFACE_TYPES.map(type => <option key={type.id} value={type.id}>{type.label}</option>)}</optgroup>
          {customTypes.length > 0 && <optgroup label="自定义类型">{customTypes.map(type => <option key={type.id} value={type.id}>{type.label}</option>)}</optgroup>}
        </select></label>
        <label>新建自定义类型<input aria-label={`${item.name}：新建自定义接口类型`} maxLength={80} value={customTypeLabel} disabled={!editable} placeholder="输入类型名称" onChange={event => setCustomTypeLabel(event.currentTarget.value)}/></label>
        <button type="button" className="ghost compact-action" disabled={!editable || !customTypeLabel.trim() || customTypeLabel.trim().length > 80} onClick={() => {
          const label = customTypeLabel.trim().replace(/\s+/g, " ");
          onSetInterfaceType(member.id, item.id, `custom:${label}`, label);
          setCustomTypeLabel("");
        }}>定义并应用</button>
        <small>类型仅用于分类展示；接口连接关系不由类型定义决定。</small>
      </div>;
    })()}
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
          {members.flatMap(member => member.interfaces.map(item => <option key={`${member.id}:${item.id}`} value={sourceKey({ memberId: member.id, interfaceId: item.id })}>{member.name} · {item.name} · {interfaceTypeLabel(item.connectionTypeId, member.interfaces, interfaceTypes)}</option>))}
        </select></label>
        {baseInterface && <button type="button" className="slot-preview-link" onClick={() => onSelectInterface(baseInterface)}>预览基准接口 · {sourceLabel(members, baseInterface)}</button>}
        <label>占用接口<select aria-label={`${slot.name}：占用接口`} value={assignmentValue} disabled={!editable || !basePart || candidates.length === 0} onChange={event => { const next = parseSource(event.currentTarget.value); onPatchSlot(slot.id, next ? { memberId: next.memberId, interfaceId: next.interfaceId } : { memberId: null, interfaceId: null }); }}>
          <option value="">{basePart ? "未分配占用接口" : "请先选择基准接口"}</option>
          {candidates.map(candidate => <option key={`${candidate.member.id}:${candidate.item.id}`} value={sourceKey({ memberId: candidate.member.id, interfaceId: candidate.item.id })}>{candidate.member.name} · {candidate.item.name} · {interfaceTypeLabel(candidate.item.connectionTypeId, candidate.member.interfaces, interfaceTypes)}{candidate.result.reason === "几何类型匹配；尺寸未声明" ? " · 尺寸未声明" : ""}</option>)}
        </select></label>
        {assigned && <button type="button" className="slot-preview-link" onClick={() => onSelectInterface(assigned)}>预览占用接口 · {sourceLabel(members, assigned)}</button>}
        {!baseInterface && <small className="slot-status warning">请先选择基准接口</small>}
        {baseInterface && !assigned && slot.required && <small className="slot-status warning">必填槽位尚未分配兼容接口</small>}
        {basePart && !candidates.length && <small className="slot-status warning">{unavailableMessage}</small>}
        {unavailable.length > 0 && <details className="slot-unavailable"><summary>查看不可用接口及原因（{unavailable.length}）</summary>{unavailable.map(candidate => <small key={`${candidate.member.id}:${candidate.item.id}`}>{candidate.member.name} · {candidate.item.name} · {interfaceTypeLabel(candidate.item.connectionTypeId, candidate.member.interfaces, interfaceTypes)}：{candidate.occupied ? `已被槽位“${candidate.occupied.name}”占用` : candidate.result.reason}</small>)}</details>}
        {assigned && <small className="slot-status">已分配：{sourceLabel(members, assigned)}</small>}
      </div>;
    })}
    {slots.length === 0 && <p className="empty-text">暂无槽位，点击“新增槽位”开始配置</p>}
  </>;
}
