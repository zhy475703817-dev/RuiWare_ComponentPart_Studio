import { useState } from "react";
import type { ComponentMember, MemberParameterDefinition, ParameterBinding, ParameterValue } from "../domain/component.ts";
import { memberParameterPatch } from "../domain/memberParameterEdit.ts";

type Props = {
  members: ComponentMember[];
  bindings: ParameterBinding[];
  parameters: { id: string; label: string }[];
  editable: boolean;
  selectedMemberId?: string | null;
  onSelectMember?: (id: string) => void;
  onChange: (memberId: string, parameterId: string, value: ParameterValue) => void;
  onRemoveBinding?: (id: string) => void;
};

function MemberParameterField({ member, definition, bindings, parameters, editable, onChange, onRemoveBinding }: Omit<Props, "members"> & { member: ComponentMember; definition: MemberParameterDefinition }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const bound = bindings.filter(item => item.memberId === member.id && item.memberParameterId === definition.id);
  const numeric = ["number", "integer"].includes(definition.valueType);
  const fixed = numeric && definition.minimum != null && definition.minimum === definition.maximum;
  const disabled = !editable || bound.length > 0 || fixed;
  const value = member.parameterValues[definition.id] ?? definition.default;
  const apply = (value: ParameterValue) => {
    if (!memberParameterPatch(member, bindings, definition.id, value)) { setError("请输入符合类型和范围的有效值。"); return false; }
    onChange(member.id, definition.id, value); setError(""); return true;
  };
  return <div className="parameter-control">
    <label htmlFor={`member-parameter-${member.id}-${definition.id}`}>{definition.label}{definition.unit && `（${definition.unit}）`}</label>
    <div className="parameter-value-with-reset">{definition.valueType === "boolean"
      ? <input id={`member-parameter-${member.id}-${definition.id}`} type="checkbox" disabled={disabled} checked={Boolean(value)} onChange={event => apply(event.currentTarget.checked)}/>
      : <input id={`member-parameter-${member.id}-${definition.id}`} type={numeric ? "number" : "text"} disabled={disabled} value={draft ?? String(value)}
          min={definition.minimum ?? undefined} max={definition.maximum ?? undefined} step={definition.valueType === "integer" ? 1 : "any"}
          aria-invalid={Boolean(error)} onFocus={event => setDraft(event.currentTarget.value)} onChange={event => { setDraft(event.currentTarget.value); setError(""); }}
          onBlur={event => {
            if (draft === null) return;
            if (numeric && !event.currentTarget.value.trim()) { setDraft(null); return; }
            if (apply(numeric ? event.currentTarget.valueAsNumber : event.currentTarget.value)) setDraft(null);
          }} onKeyDown={event => {
            if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
            if (event.key === "Escape") { event.preventDefault(); setDraft(null); setError(""); }
          }}/>}
    <button type="button" className="ghost" aria-label={`初始化${definition.label}`} disabled={!editable || bound.length > 0} title={bound.length ? "由组件参数控制，请先解除绑定再初始化" : `恢复模板默认值：${String(definition.default)}`} onMouseDown={event => event.preventDefault()} onClick={() => { setDraft(null); apply(definition.default); }}>初始化</button></div>
    {numeric && <small className="parameter-range">{fixed ? "固定参数" : "有效范围"}：{definition.minimum ?? "不限"} ～ {definition.maximum ?? "不限"}{definition.valueType === "integer" ? "，仅整数" : ""}</small>}
    {bound.length > 0 && <><small className="hint">由组件参数 {bound.map(binding => parameters.find(item => item.id === binding.parameterId)?.label || binding.parameterId).join("、")} 控制</small>
      {onRemoveBinding && <button type="button" className="ghost" disabled={!editable} onClick={() => { bound.forEach(binding => onRemoveBinding(binding.id)); setDraft(null); setError(""); }}>解除绑定后独立编辑</button>}</>}
    {error && <small role="alert">{error}</small>}
  </div>;
}

export function MemberParametersEditor(props: Props) {
  const member = props.members.find(item => item.id === props.selectedMemberId);
  return <section className="parameter-member-editor" aria-label="零部件参数编辑">
    <h4>零部件参数编辑</h4>
    <label htmlFor="member-parameter-selection">选择零部件</label><select id="member-parameter-selection" value={member?.id || ""} disabled={!props.members.length} onChange={event => props.onSelectMember?.(event.currentTarget.value)}>
      <option value="">请选择零部件</option>{props.members.map(item => <option key={item.id} value={item.id}>{item.name} · {item.code}</option>)}
    </select>
    {!props.members.length ? <p className="empty-text">添加零部件后可编辑参数。</p> : !member ? <p className="hint">选择零部件后查看其参数。</p> : <>
      <p className="hint">直接输入，按 Enter 或移开焦点应用；Esc 取消。模型与接口位置会按新参数更新。</p>
      {!member.parameterDefinitions?.length && <p className="empty-text">该零部件未提供可编辑参数定义。</p>}
      {member.parameterDefinitions?.map(definition => <MemberParameterField key={`${member.id}:${definition.id}`} {...props} member={member} definition={definition}/>)}
    </>}
  </section>;
}
