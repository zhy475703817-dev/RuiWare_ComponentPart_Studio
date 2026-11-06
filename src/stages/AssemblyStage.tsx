import { useMemo, useState } from "react";
import { Link2 } from "lucide-react";
import type { ComponentMember, Mate, PartInterface } from "../domain/component";
import { assessInterfaceCompatibility, type CompatibilityAssessment } from "../domain/interfaceCompatibility";

type Props = { members: ComponentMember[]; mates: Mate[]; onAddMate: (mate: Mate) => boolean };
type Endpoint = { memberId: string; interfaceId: string };

function endpointKey(value: Endpoint) { return `${value.memberId}\u0000${value.interfaceId}`; }
function interfaceLabel(member: ComponentMember, item: PartInterface) { return `${member.role} / ${member.name} / ${item.name} (${item.id})`; }
function statusLabel(result: CompatibilityAssessment) {
  return result.status === "compatible" ? "兼容" : result.status === "needs_review" ? "待复核" : "不兼容";
}

export function AssemblyStage({ members, mates, onAddMate }: Props) {
  const endpoints = useMemo(() => members.flatMap(member => member.interfaces.map(item => ({ member, item, value: { memberId: member.id, interfaceId: item.id } }))), [members]);
  const [fromKey, setFromKey] = useState("");
  const [toKey, setToKey] = useState("");
  const [relation, setRelation] = useState<"贴合" | "同轴" | "重合">("贴合");
  const from = endpoints.find(item => endpointKey(item.value) === fromKey);
  const candidates = from ? endpoints.filter(item => item.member.id !== from.member.id) : [];
  const to = candidates.find(item => endpointKey(item.value) === toKey);
  const assessment = from && to ? assessInterfaceCompatibility(from.item, to.item, relation, from.member, to.member) : undefined;
  const availableRelations = from && to ? (["贴合", "同轴", "重合"] as const).filter(value =>
    assessInterfaceCompatibility(from.item, to.item, value, from.member, to.member).status === "compatible") : [];
  const alreadyConnected = from && to && mates.some(mate =>
    mate.from.memberId === from.value.memberId && mate.from.interfaceId === from.value.interfaceId &&
    mate.to.memberId === to.value.memberId && mate.to.interfaceId === to.value.interfaceId ||
    mate.to.memberId === from.value.memberId && mate.to.interfaceId === from.value.interfaceId &&
    mate.from.memberId === to.value.memberId && mate.from.interfaceId === to.value.interfaceId);
  const addMate = () => {
    if (!from || !to || assessment?.status !== "compatible" || alreadyConnected) return;
    const added = onAddMate({ id: crypto.randomUUID(), from: from.value, to: to.value, relation, offset: 0 });
    if (added) setToKey("");
  };
  return <>
    <div className="callout">先明确选择两端接口。只有契约规则明确判定为兼容时才能记录连接；成功只表示“声明兼容，未求解”，不会自动改变成员位置。</div>
    <h4>新建接口连接</h4>
    <div className="form-grid">
      <label>接口 A<select value={fromKey} onChange={event => { setFromKey(event.target.value); setToKey(""); }}>
        <option value="">选择成员接口</option>
        {endpoints.map(({ member, item, value }) => <option key={endpointKey(value)} value={endpointKey(value)}>{interfaceLabel(member, item)}</option>)}
      </select></label>
      <label>接口 B<select value={toKey} onChange={event => setToKey(event.target.value)} disabled={!from}>
        <option value="">选择另一成员接口</option>
        {candidates.map(({ member, item, value }) => <option key={endpointKey(value)} value={endpointKey(value)}>{interfaceLabel(member, item)}</option>)}
      </select></label>
      <label>连接关系<select value={relation} onChange={event => setRelation(event.target.value as typeof relation)} disabled={!availableRelations.length}>
        {(["贴合", "同轴", "重合"] as const).map(value => <option key={value} value={value} disabled={Boolean(from && to) && !availableRelations.includes(value)}>{value}</option>)}
      </select></label>
    </div>
    {assessment && <div className={`callout ${assessment.status === "compatible" ? "" : "validation-error"}`} role="status">
      <b>{statusLabel(assessment)}</b> · {assessment.reason}
      <small>{assessment.checks.map(item => item.message).join("；")}</small>
    </div>}
    {alreadyConnected && <p className="hint">这两个接口已经存在连接关系。</p>}
    <div className="action-row"><button className="primary" onClick={addMate} disabled={!from || !to || assessment?.status !== "compatible" || Boolean(alreadyConnected)}><Link2 size={15}/>记录兼容连接</button></div>
    <h4>接口连接</h4>
    {mates.length === 0 ? <p className="empty-text">尚未建立内部连接</p> : mates.map(mate => {
      const a = endpoints.find(item => endpointKey(item.value) === endpointKey(mate.from));
      const b = endpoints.find(item => endpointKey(item.value) === endpointKey(mate.to));
      const result = assessInterfaceCompatibility(a?.item, b?.item, mate.relation, a?.member, b?.member);
      const connectionStatus = result.status === "compatible" ? "声明兼容，未求解"
        : result.status === "needs_review" ? "历史连接，未按新契约评估" : "当前契约判定不兼容";
      return <div className="info-row" key={mate.id}><Link2 size={14}/><span>{a ? interfaceLabel(a.member, a.item) : `${mate.from.memberId}.${mate.from.interfaceId}`} <b>→</b> {b ? interfaceLabel(b.member, b.item) : `${mate.to.memberId}.${mate.to.interfaceId}`}<small>{mate.relation} · 偏置 {mate.offset} mm · {connectionStatus}</small></span></div>;
    })}
  </>;
}
