import { useState } from "react";
import { PackageCheck, X } from "lucide-react";
import type { ComponentMember, ComponentPort, GeometryDesign, InterfaceSelection, Mate } from "../domain/component";
import { groupHardwareUsage, summarizeHardwareUsage } from "../domain/hardware";
import { auditHardwareSelection } from "../domain/hardwareSelection";

export function HardwareSummary({ members, mates, geometryDesign, ports = [], inline = false, onSelectInterface }: { members: ComponentMember[]; mates: Mate[]; geometryDesign?: GeometryDesign | null; ports?: ComponentPort[]; inline?: boolean; onSelectInterface?: (selection: InterfaceSelection) => void }) {
  const [hardwareOpen, setHardwareOpen] = useState(false);
  const hardwareUsage = summarizeHardwareUsage(members, mates, geometryDesign);
  const hardwareGroups = groupHardwareUsage(hardwareUsage);
  const hardwareTotal = hardwareUsage.reduce((total, item) => total + item.quantity, 0);
  const audit = auditHardwareSelection(members, mates, geometryDesign, ports);
  const statusPanel = <details className="hardware-selection-summary" aria-label="五金配置完成情况">
    <summary>五金配置 · {audit.pending > 0 ? `${audit.pending} 个待确认` : `${audit.total} 个节点已确认`}</summary>
    <p role="status">共 {audit.total} 个已连接节点：已指定 {audit.configured}，无需额外五金 {audit.notRequired}，待确认 {audit.pending}。</p>
    <small>已连接接口需要五金时必须填写完整，未完成不能发布。未连接的预留接口不强制配置；不检查尺寸适用性。</small>
    {audit.entries.filter(entry => entry.status === "pending").map(entry => <details key={entry.id}>
      <summary>{entry.label}</summary>
      {entry.issues.map((issue, index) => <small key={`${issue.code}-${index}`}>{issue.message}</small>)}
      {onSelectInterface && entry.endpoints.filter(endpoint => endpoint.path.length === 1).map(endpoint => <button type="button" className="ghost compact-action" key={`${endpoint.path[0]}:${endpoint.interfaceId}`} onClick={() => { onSelectInterface({ memberId: endpoint.path[0], interfaceId: endpoint.interfaceId }); setHardwareOpen(false); }}>配置 {endpoint.label}</button>)}
    </details>)}
  </details>;
  if (inline) return <section className="parameter-hardware" aria-label="五金用量统计">
    <div className="parameter-hardware-heading"><h4><PackageCheck size={14}/>五金用量统计</h4><small>自动更新</small></div>
    <p className="parameter-hardware-note">当前模板的全部五金用量（含子组件），随装配和五金配置自动更新。同名称、同规格算一种，共用五金只计一次。</p>
    {statusPanel}
    <div className="stats" role="status" aria-live="polite" aria-atomic="true">
      <div><b>{hardwareGroups.length}</b><span>类别（类）</span></div>
      <div><b>{hardwareUsage.length}</b><span>种数（种）</span></div>
      <div><b>{hardwareTotal}</b><span>总数量（件）</span></div>
    </div>
    {hardwareGroups.length === 0 ? <p className="empty-text">当前模板暂无五金用量。</p> : hardwareGroups.map(group => <div className="parameter-hardware-group" key={group.category}>
      <div className="parameter-hardware-group-heading"><strong>{group.category}</strong><span>{group.varieties} 种 · {group.quantity} 件</span></div>
      <ul>{group.items.map(item => <li key={JSON.stringify([item.name, item.specification])}><span>{item.name}<small>{item.specification}</small></span><b>{item.quantity} 件</b></li>)}</ul>
    </div>)}
  </section>;
  return <>
    <button type="button" className="ghost compact-action" onClick={() => setHardwareOpen(true)}><PackageCheck size={14}/>五金用量统计{audit.pending > 0 ? ` · ${audit.pending} 待确认` : ""}</button>
    {hardwareOpen && <div className="hardware-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setHardwareOpen(false); }}>
      <section className="hardware-modal" role="dialog" aria-modal="true" aria-labelledby="hardware-modal-title">
        <header><span><PackageCheck size={18}/><b id="hardware-modal-title">五金用量统计</b></span><button type="button" className="icon-button" aria-label="关闭五金统计" onClick={() => setHardwareOpen(false)}><X size={17}/></button></header>
        <p>汇总当前模板的全部五金（含子组件），按类别显示种数、规格明细和件数，随装配和配置自动更新。共用五金只计一次。</p>
        {statusPanel}
        {hardwareUsage.length === 0 ? <div className="hardware-empty"><PackageCheck size={24}/><b>当前模板暂无五金用量</b></div> : <>
          <div className="hardware-summary"><span><b>{hardwareGroups.length}</b>类五金</span><span><b>{hardwareUsage.length}</b>种五金</span><span><b>{hardwareTotal}</b>件合计</span></div>
          <div className="hardware-table-wrap"><table className="hardware-table"><thead><tr><th>五金类别</th><th>种数</th><th>名称与规格</th><th>件数小计</th></tr></thead><tbody>{hardwareGroups.map(group => <tr key={group.category}><td>{group.category}</td><td>{group.varieties}</td><td>{group.items.map(item => `${item.name} · ${item.specification} × ${item.quantity}`).join("；")}</td><td>{group.quantity}</td></tr>)}</tbody></table></div>
        </>}
        <footer><button type="button" className="primary" onClick={() => setHardwareOpen(false)}>完成</button></footer>
      </section>
    </div>}
  </>;
}
