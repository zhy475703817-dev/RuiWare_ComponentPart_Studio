import { useState } from "react";
import type { ComponentMember, ComponentParameter, ComponentPattern, ParameterBinding } from "../domain/component.ts";
import { deriveComponentMetrics, effectiveParameterBounds, formatParameterValue, parameterRange, type Metric } from "../domain/parameterMetrics.ts";

type Props = { parameters: ComponentParameter[]; bindings: ParameterBinding[]; patterns?: ComponentPattern[]; members?: ComponentMember[]; onChange: (id: string, value: number | null) => void; onBindingChange?: (id: string, binding: ParameterBinding | null) => void; onAddParameter?: (parameter: ComponentParameter) => void; onRemoveParameter?: (id: string) => void; onAddPattern?: (pattern: ComponentPattern) => void; onRemovePattern?: (id: string) => void };

function clampValue(parameter: ComponentParameter, value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.min(parameter.max, Math.max(parameter.min, value));
}

function ParameterControl({ parameter, bindings, patterns, members, onChange }: { parameter: ComponentParameter; bindings: ParameterBinding[]; patterns: ComponentPattern[]; members: ComponentMember[]; onChange: Props["onChange"] }) {
  const bounds = effectiveParameterBounds(parameter, bindings, members);
  const step = patterns.some(pattern => pattern.countParameterId === parameter.id) || bindings.some(binding => binding.parameterId === parameter.id && members.some(member => member.id === binding.memberId && member.parameterDefinitions?.some(definition => definition.id === binding.memberParameterId && definition.valueType === "integer"))) ? 1 : "any";
  const hasSlider = Number.isFinite(bounds.min) && Number.isFinite(bounds.max) && bounds.max > bounds.min;
  const safeValue = parameter.value ?? bounds.min;
  const effectiveParameter = { ...parameter, min: bounds.min, max: bounds.max };
  const update = (value: number | null) => {
    const bounded = clampValue(effectiveParameter, value);
    onChange(parameter.id, bounded !== null && step === 1 ? Math.round(bounded) : bounded);
  };
  return <div className="parameter-control"><div className="parameter-control-heading"><label htmlFor={`parameter-${parameter.id}`}>{parameter.label}<small>{parameter.unit}</small></label><output htmlFor={`parameter-${parameter.id}`}>{formatParameterValue(parameter)}</output></div>
    {hasSlider && <input className="parameter-slider" type="range" min={bounds.min} max={bounds.max} step={step} value={safeValue} aria-label={`${parameter.label}滑块`} onChange={event => update(event.currentTarget.valueAsNumber)} />}
    <input id={`parameter-${parameter.id}`} type="number" value={parameter.value ?? ""} min={bounds.min} max={bounds.max} step={step} onChange={event => update(event.target.value === "" ? null : event.target.valueAsNumber)} />
    <small className="parameter-range">有效范围：{parameterRange(effectiveParameter) || "未提供范围"}{bounds.sources.length > 0 && `（继承自：${bounds.sources.join("、")}）`}</small></div>;
}

function MetricRow({ metric }: { metric: Metric }) { return <div className="metric-row"><span>{metric.label}<small>{metric.note || ""}</small></span><b>{metric.value}</b></div>; }

export function ParametersStage({ parameters, bindings, patterns = [], members = [], onChange, onBindingChange, onAddParameter, onRemoveParameter, onAddPattern, onRemovePattern }: Props) {
  const [newParameter, setNewParameter] = useState({ id: "", label: "", unit: "mm", min: "0", max: "100", value: "0" });
  const [newPattern, setNewPattern] = useState({ id: "", label: "", countParameterId: "", spacingParameterId: "", axis: "z" as ComponentPattern["axis"], memberIds: [] as string[] });
  const metrics = deriveComponentMetrics(parameters, bindings, members);
  const addParameter = () => {
    const min = Number(newParameter.min), max = Number(newParameter.max), value = Number(newParameter.value);
    if (!newParameter.id.trim() || !newParameter.label.trim() || !Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(value) || min > max || value < min || value > max || parameters.some(parameter => parameter.id === newParameter.id.trim())) return;
    onAddParameter?.({ id: newParameter.id.trim(), label: newParameter.label.trim(), unit: newParameter.unit.trim() || "mm", min, max, value });
    setNewParameter({ id: "", label: "", unit: "mm", min: "0", max: "100", value: "0" });
  };
  const addPattern = () => {
    const count = parameters.find(parameter => parameter.id === newPattern.countParameterId);
    const spacing = parameters.find(parameter => parameter.id === newPattern.spacingParameterId);
    if (!newPattern.id.trim() || !newPattern.label.trim() || !newPattern.countParameterId || !newPattern.spacingParameterId || newPattern.memberIds.length === 0 || patterns.some(pattern => pattern.id === newPattern.id.trim()) || !count || count.value === null || !Number.isInteger(count.value) || count.min < 1 || count.value < count.min || count.value > count.max || !spacing || spacing.value === null || spacing.min < 0 || spacing.value < spacing.min || spacing.value > spacing.max) return;
    onAddPattern?.({ ...newPattern, id: newPattern.id.trim(), label: newPattern.label.trim() });
    setNewPattern({ id: "", label: "", countParameterId: "", spacingParameterId: "", axis: "z", memberIds: [] });
  };
  return <>
    <div className="parameter-intro">组件参数可绑定到已发布零部件的数值参数。绑定后，修改组件值会按成员参数定义的范围传递到成员；未接入几何重建的成员不会自动改变模型。</div>
    <div className="parameter-controls">
      {parameters.length === 0 && <p className="empty-text">当前组件没有可调整参数。</p>}
      {parameters.map(parameter => <ParameterControl key={parameter.id} parameter={parameter} bindings={bindings} patterns={patterns} members={members} onChange={onChange} />)}
    </div>
    <section className="parameter-metrics" aria-label="组件变化参数"><h4>组件变化参数</h4><MetricRow metric={metrics.length}/><MetricRow metric={metrics.width}/><MetricRow metric={metrics.height}/><MetricRow metric={metrics.volume}/><MetricRow metric={metrics.weight}/></section>
    <h4>成员参数绑定</h4>
    {bindings.length === 0 && <p className="empty-text">暂无绑定。绑定完成后，组件参数可驱动对应零部件参数。</p>}
    {parameters.map(parameter => <div className="info-row" key={`parameter-action-${parameter.id}`}><span>{parameter.label}<small>{parameter.id}</small></span>{onRemoveParameter && <button type="button" className="ghost" onClick={() => onRemoveParameter(parameter.id)}>删除参数</button>}</div>)}
    {bindings.map(binding => <div className="info-row" key={binding.id}><span>{binding.parameterId}<small>→ {binding.memberId} / {binding.memberParameterId}</small></span>{onBindingChange && <button type="button" className="ghost" onClick={() => onBindingChange(binding.id, null)}>移除</button>}</div>)}
    {patterns.length > 0 && <><h4>组件构成规则</h4>{patterns.map(pattern => <div className="info-row" key={pattern.id}><span>{pattern.label}<small>{pattern.memberIds.length} 个成员 · {pattern.axis} 轴 · {pattern.countParameterId} × {pattern.spacingParameterId}</small></span>{onRemovePattern && <button type="button" className="ghost" onClick={() => onRemovePattern(pattern.id)}>移除</button>}</div>)}</>}
    {onAddParameter && <div className="parameter-add"><h4>新增组件参数</h4><div className="parameter-add-grid"><input placeholder="稳定 ID" value={newParameter.id} onChange={event => setNewParameter({ ...newParameter, id: event.target.value })} /><input placeholder="显示名称" value={newParameter.label} onChange={event => setNewParameter({ ...newParameter, label: event.target.value })} /><input placeholder="单位" value={newParameter.unit} onChange={event => setNewParameter({ ...newParameter, unit: event.target.value })} /><input type="number" placeholder="最小值" value={newParameter.min} onChange={event => setNewParameter({ ...newParameter, min: event.target.value })} /><input type="number" placeholder="最大值" value={newParameter.max} onChange={event => setNewParameter({ ...newParameter, max: event.target.value })} /><input type="number" placeholder="默认值" value={newParameter.value} onChange={event => setNewParameter({ ...newParameter, value: event.target.value })} /></div><button type="button" className="ghost" onClick={addParameter}>添加参数</button></div>}
    {onBindingChange && members.some(member => (member.parameterDefinitions || []).some(definition => ["number", "integer"].includes(definition.valueType))) && <div className="binding-picker"><label>新增绑定<select defaultValue="" onChange={event => { const [parameterId, memberId, memberParameterId] = event.target.value.split("::"); if (!parameterId || !memberId || !memberParameterId) return; const bindingId = crypto.randomUUID(); onBindingChange(bindingId, { id: bindingId, parameterId, memberId, memberParameterId }); event.currentTarget.value = ""; }}><option value="">选择组件参数 → 成员参数</option>{parameters.flatMap(parameter => members.flatMap(member => (member.parameterDefinitions || []).filter(definition => definition.unit === parameter.unit && ["number", "integer"].includes(definition.valueType)).map(definition => { const binding = { id: "candidate", parameterId: parameter.id, memberId: member.id, memberParameterId: definition.id }; const bounds = effectiveParameterBounds(parameter, [...bindings, binding], members); const duplicate = bindings.some(item => item.parameterId === parameter.id && item.memberId === member.id && item.memberParameterId === definition.id); return <option key={`${parameter.id}::${member.id}::${definition.id}`} value={`${parameter.id}::${member.id}::${definition.id}`} disabled={duplicate || bounds.min > bounds.max}>{parameter.label} → {member.name}.{definition.label}{bounds.min > bounds.max ? "（范围无交集）" : ""}</option>; })))}</select></label></div>}
    {onAddPattern && members.length > 0 && <div className="parameter-add"><h4>新增组件构成规则</h4><div className="parameter-add-grid"><input placeholder="稳定 ID" value={newPattern.id} onChange={event => setNewPattern({ ...newPattern, id: event.target.value })} /><input placeholder="显示名称" value={newPattern.label} onChange={event => setNewPattern({ ...newPattern, label: event.target.value })} /><select value={newPattern.countParameterId} onChange={event => setNewPattern({ ...newPattern, countParameterId: event.target.value })}><option value="">数量参数</option>{parameters.filter(parameter => ["个", "件"].includes(parameter.unit)).map(parameter => <option key={parameter.id} value={parameter.id}>{parameter.label}</option>)}</select><select value={newPattern.spacingParameterId} onChange={event => setNewPattern({ ...newPattern, spacingParameterId: event.target.value })}><option value="">间距参数</option>{parameters.filter(parameter => parameter.unit === "mm").map(parameter => <option key={parameter.id} value={parameter.id}>{parameter.label}</option>)}</select><select value={newPattern.axis} onChange={event => setNewPattern({ ...newPattern, axis: event.target.value as ComponentPattern["axis"] })}><option value="x">X 轴</option><option value="y">Y 轴</option><option value="z">Z 轴</option></select></div><div className="pattern-members">{members.map(member => <label key={member.id}><input type="checkbox" checked={newPattern.memberIds.includes(member.id)} onChange={event => setNewPattern({ ...newPattern, memberIds: event.target.checked ? [...newPattern.memberIds, member.id] : newPattern.memberIds.filter(id => id !== member.id) })}/>{member.name}</label>)}</div><button type="button" className="ghost" onClick={addPattern}>添加构成规则</button></div>}
    <div className="callout">长度、宽度和高度显示对应参数值，不代表装配包络尺寸。体积和重量需要实体几何、材料密度等数据后才能可靠计算。</div>
  </>;
}
