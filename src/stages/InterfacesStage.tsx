import { CheckCircle2 } from "lucide-react";
import type { ComponentPort, ComponentSlot } from "../domain/component";

export function InterfacesStage({ ports, slots }: { ports: ComponentPort[]; slots: ComponentSlot[] }) {
  return <>
    <div className="callout">组件接口保留其来源成员和零部件接口引用，发布版本保持固定。</div>
    <h4>对外接口</h4>
    {ports.map(port => <div className="info-row" key={port.id}><CheckCircle2 size={14}/><span>{port.name}<small>来源：{port.source.memberId} / {port.source.interfaceId}</small><small>{port.tags.join(", ")}</small>{port.interface && <small>{[port.interface.interfaceType || port.interface.type, port.interface.locatingType, port.interface.region?.mode, port.interface.region?.uSpan !== undefined && port.interface.region?.vSpan !== undefined ? `${port.interface.region.uSpan} × ${port.interface.region.vSpan} mm` : undefined, port.interface.region?.uSpanExpression || port.interface.region?.vSpanExpression ? `U ${port.interface.region?.uSpanExpression || "?"} × V ${port.interface.region?.vSpanExpression || "?"}` : undefined, port.interface.region?.countExpression ? `数量表达式 ${port.interface.region.countExpression}` : undefined].filter(Boolean).join(" / ")}</small>}</span></div>)}
    {ports.length === 0 && <p className="empty-text">在装配阶段选择成员后点击“继承所选接口”</p>}
    <h4>可替换槽位</h4>
    {slots.map(slot => <div className="info-row" key={slot.id}><span>{slot.name}<small>{slot.required ? "必填" : "可选"} · {slot.memberId || "未放置成员"}</small></span></div>)}
    {slots.length === 0 && <p className="empty-text">暂无槽位，槽位编辑尚未接入</p>}
  </>;
}
