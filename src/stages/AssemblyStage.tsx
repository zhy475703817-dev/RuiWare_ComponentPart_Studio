import { Link2 } from "lucide-react";
import type { ComponentMember, Mate, ParameterBinding } from "../domain/component";

type Props = { mates: Mate[]; onConnect: () => void; onInherit: () => void;
  selectedMember?: ComponentMember; bindings: ParameterBinding[];
  onMemberChange: (patch: Partial<Pick<ComponentMember, "position" | "rotation" | "parameterValues">>) => void };

export function AssemblyStage({ mates, onConnect, onInherit, selectedMember: member, bindings, onMemberChange }: Props) {
  return <>
    <div className="callout">从左侧加入零部件，在画布中选择成员，可拖动操作轴或输入数值调整位置。</div>
    {member && <section className="assembly-member-editor" aria-label="所选成员设置">
      <h4>{member.name} · V{member.template.version}</h4>
      {(["position", "rotation"] as const).map(field => <div key={field} className="assembly-vector">
        {["X", "Y", "Z"].map((axis, index) => <label key={axis}>{field === "position" ? "位置" : "旋转"} {axis}（{field === "position" ? "mm" : "°"}）
          <input type="number" step="any" value={Number((member[field][index] * (field === "rotation" ? 180 / Math.PI : 1)).toFixed(6))}
            onChange={event => {
              if (!Number.isFinite(event.target.valueAsNumber)) return;
              const vector: ComponentMember[typeof field] = [...member[field]];
              vector[index] = event.target.valueAsNumber * (field === "rotation" ? Math.PI / 180 : 1);
              onMemberChange({ [field]: vector });
            }}/>
        </label>)}
      </div>)}
      {member.parameterDefinitions?.map(parameter => {
        const bound = bindings.some(binding => binding.memberId === member.id && binding.memberParameterId === parameter.id);
        const value = member.parameterValues[parameter.id] ?? parameter.default;
        const numeric = ["number", "integer"].includes(parameter.valueType);
        const update = (next: string | number | boolean) => onMemberChange({ parameterValues: { ...member.parameterValues, [parameter.id]: next } });
        return <label key={parameter.id}>{parameter.label} {parameter.unit}
          {parameter.valueType === "boolean"
            ? <input type="checkbox" aria-label={`成员参数 ${parameter.label}`} checked={Boolean(value)} disabled={bound} onChange={event => update(event.target.checked)}/>
            : <input aria-label={`成员参数 ${parameter.label}`} type={numeric ? "number" : "text"} value={String(value)} disabled={bound}
                min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.valueType === "integer" ? 1 : "any"}
                onChange={event => { if (!numeric || event.target.validity.valid && Number.isFinite(event.target.valueAsNumber)) update(numeric ? event.target.valueAsNumber : event.target.value); }}/>}
          {bound && <small className="hint">由组件参数统一控制，请在“参数”阶段调整。</small>}
        </label>;
      })}
    </section>}
    <div className="action-row"><button className="primary" onClick={onConnect}><Link2 size={15}/>连接前两个接口</button><button className="ghost" onClick={onInherit}>继承所选接口</button></div>
    <h4>接口连接</h4>
    {mates.length === 0 ? <p className="empty-text">尚未建立内部连接</p> : mates.map(mate => <div className="info-row" key={mate.id}><Link2 size={14}/><span>{mate.from.interfaceId} <b>→</b> {mate.to.interfaceId}<small>{mate.relation} · 偏置 {mate.offset} mm · 未求解</small></span></div>)}
  </>;
}
