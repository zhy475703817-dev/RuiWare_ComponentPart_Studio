import { Link2 } from "lucide-react";
import type { Mate } from "../domain/component";

type Props = { mates: Mate[]; onConnect: () => void; onInherit: () => void };

export function AssemblyStage({ mates, onConnect, onInherit }: Props) {
  return <>
    <div className="callout">可从左侧发布详情加入零部件或组件，并记录内部连接和继承已有接口。装配求解尚未接入。</div>
    <div className="action-row"><button className="primary" onClick={onConnect}><Link2 size={15}/>连接前两个接口</button><button className="ghost" onClick={onInherit}>继承所选接口</button></div>
    <h4>接口连接</h4>
    {mates.length === 0 ? <p className="empty-text">尚未建立内部连接</p> : mates.map(mate => <div className="info-row" key={mate.id}><Link2 size={14}/><span>{mate.from.interfaceId} <b>→</b> {mate.to.interfaceId}<small>{mate.relation} · 偏置 {mate.offset} mm · 未求解</small></span></div>)}
  </>;
}
