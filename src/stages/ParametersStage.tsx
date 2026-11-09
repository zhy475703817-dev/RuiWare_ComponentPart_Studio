import { useState } from "react";
import type { ComponentMember, ComponentParameter, ComponentPattern, ComponentRule, ParameterBinding } from "../domain/component.ts";
import { deriveComponentMetrics, effectiveParameterBounds, formatParameterValue, parameterRange, type Metric } from "../domain/parameterMetrics.ts";

type Props = {
  parameters: ComponentParameter[];
  bindings: ParameterBinding[];
  patterns?: ComponentPattern[];
  rules?: ComponentRule[];
  members?: ComponentMember[];
  definitionName?: string;
  definitionCode?: string;
  onChange: (id: string, value: number | null) => void;
  onBindingChange?: (id: string, binding: ParameterBinding | null) => void;
  onAddParameter?: (parameter: ComponentParameter) => void;
  onRemoveParameter?: (id: string) => void;
  onAddPattern?: (pattern: ComponentPattern) => void;
  onRemovePattern?: (id: string) => void;
  onAddRule?: (rule: ComponentRule) => void;
  onRemoveRule?: (id: string) => void;
};

const dimensionAliases: Record<"length" | "width" | "height", string[]> = {
  length: ["length", "overallLength", "componentLength", "总长", "长度"],
  width: ["width", "overallWidth", "componentWidth", "总宽", "宽度"],
  height: ["height", "overallHeight", "componentHeight", "总高", "高度"],
};

function clampValue(parameter: ComponentParameter, value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.min(parameter.max, Math.max(parameter.min, value));
}

function isDimension(parameter: ComponentParameter, axis: keyof typeof dimensionAliases): boolean {
  const key = `${parameter.id} ${parameter.label}`.toLowerCase();
  return dimensionAliases[axis].some(alias => key.includes(alias.toLowerCase()));
}

function isDerivedParameter(parameter: ComponentParameter): boolean {
  const key = `${parameter.id} ${parameter.label}`.toLowerCase();
  return ["volume", "体积", "weight", "mass", "重量", "质量"].some(token => key.includes(token));
}

function ParameterControl({ parameter, bindings, patterns, members, onChange }: { parameter: ComponentParameter; bindings: ParameterBinding[]; patterns: ComponentPattern[]; members: ComponentMember[]; onChange: Props["onChange"] }) {
  const bounds = effectiveParameterBounds(parameter, bindings, members);
  const step = patterns.some(pattern => pattern.countParameterId === parameter.id) || bindings.some(binding => binding.parameterId === parameter.id && members.some(member => member.id === binding.memberId && member.parameterDefinitions?.some(definition => definition.id === binding.memberParameterId && definition.valueType === "integer"))) ? 1 : "any";
  const hasSlider = Number.isFinite(bounds.min) && Number.isFinite(bounds.max) && bounds.max > bounds.min;
  const safeValue = parameter.value ?? bounds.min;
  const update = (value: number | null) => {
    const bounded = clampValue({ ...parameter, min: bounds.min, max: bounds.max }, value);
    onChange(parameter.id, bounded !== null && step === 1 ? Math.round(bounded) : bounded);
  };
  return <div className="parameter-control"><div className="parameter-control-heading"><label htmlFor={`parameter-${parameter.id}`}>{parameter.label}<small>{parameter.unit}</small></label><output htmlFor={`parameter-${parameter.id}`}>{formatParameterValue(parameter)}</output></div>
    {hasSlider && <input className="parameter-slider" type="range" min={bounds.min} max={bounds.max} step={step} value={safeValue} aria-label={`${parameter.label}滑块`} onChange={event => update(event.currentTarget.valueAsNumber)} />}
    <input id={`parameter-${parameter.id}`} type="number" value={parameter.value ?? ""} min={bounds.min} max={bounds.max} step={step} onChange={event => update(event.target.value === "" ? null : event.target.valueAsNumber)} />
    <small className="parameter-range">有效范围：{parameterRange({ ...parameter, min: bounds.min, max: bounds.max }) || "未提供范围"}{bounds.sources.length > 0 && `（继承自：${bounds.sources.join("、")}）`}</small></div>;
}

function MetricRow({ metric }: { metric: Metric }) { return <div className="metric-row"><span>{metric.label}<small>{metric.note || ""}</small></span><b>{metric.value}</b></div>; }

function dimensionCandidates(parameter: ComponentParameter, axis: keyof typeof dimensionAliases, members: ComponentMember[]) {
  return members.flatMap(member => (member.parameterDefinitions || []).filter(definition => definition.unit === parameter.unit && ["number", "integer"].includes(definition.valueType) && dimensionAliases[axis].some(alias => definition.id.toLowerCase().includes(alias.toLowerCase()) || definition.label.toLowerCase().includes(alias.toLowerCase()))).map(definition => ({ member, definition })));
}

export function ParametersStage({ parameters, bindings, patterns = [], rules = [], members = [], definitionName, definitionCode, onChange, onBindingChange, onAddParameter, onRemoveParameter, onAddPattern, onRemovePattern, onAddRule, onRemoveRule }: Props) {
  const [newParameter, setNewParameter] = useState({ id: "", label: "", unit: "mm", min: "0", max: "100", value: "0" });
  const [newPattern, setNewPattern] = useState({ id: "", label: "", countParameterId: "", spacingParameterId: "", axis: "z" as ComponentPattern["axis"], memberIds: [] as string[] });
  const [newConstraint, setNewConstraint] = useState({ label: "", expression: "" });
  const [newCompositionRule, setNewCompositionRule] = useState({ label: "", expression: "" });
  const metrics = deriveComponentMetrics(parameters, bindings, members, patterns);
  const derivedMetric = (parameter: ComponentParameter): Metric | undefined => {
    const key = `${parameter.id} ${parameter.label}`.toLowerCase();
    if (key.includes("volume") || key.includes("体积")) return metrics.volume;
    if (key.includes("weight") || key.includes("mass") || key.includes("重量") || key.includes("质量")) return metrics.weight;
    return undefined;
  };
  const addParameter = () => {
    const min = Number(newParameter.min), max = Number(newParameter.max), value = Number(newParameter.value);
    if (!newParameter.id.trim() || !newParameter.label.trim() || !Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(value) || min > max || value < min || value > max || parameters.some(parameter => parameter.id === newParameter.id.trim())) return;
    onAddParameter?.({ id: newParameter.id.trim(), label: newParameter.label.trim(), unit: newParameter.unit.trim() || "mm", min, max, value });
    setNewParameter({ id: "", label: "", unit: "mm", min: "0", max: "100", value: "0" });
  };
  const addPattern = () => {
    const count = parameters.find(parameter => parameter.id === newPattern.countParameterId);
    const spacing = parameters.find(parameter => parameter.id === newPattern.spacingParameterId);
    if (!newPattern.id.trim() || !newPattern.label.trim() || !newPattern.countParameterId || !newPattern.spacingParameterId || newPattern.memberIds.length === 0 || patterns.some(pattern => pattern.id === newPattern.id.trim()) || !count || !["个", "件"].includes(count.unit) || count.value === null || !Number.isInteger(count.value) || count.min < 1 || count.value < count.min || count.value > count.max || !spacing || spacing.unit !== "mm" || spacing.value === null || spacing.min < 0 || spacing.value < spacing.min || spacing.value > spacing.max) return;
    onAddPattern?.({ ...newPattern, id: newPattern.id.trim(), label: newPattern.label.trim() });
    setNewPattern({ id: "", label: "", countParameterId: "", spacingParameterId: "", axis: "z", memberIds: [] });
  };
  const addRule = (kind: "constraint" | "composition") => {
    const draft = kind === "constraint" ? newConstraint : newCompositionRule;
    if (!draft.expression.trim()) return;
    onAddRule?.({ id: crypto.randomUUID(), kind, label: draft.label.trim() || (kind === "constraint" ? "其他限制条件" : "组件间构成规则"), expression: draft.expression.trim() });
    if (kind === "constraint") setNewConstraint({ label: "", expression: "" });
    else setNewCompositionRule({ label: "", expression: "" });
  };
  const toggleDimensionBinding = (parameter: ComponentParameter | undefined, member: ComponentMember, definition: NonNullable<ComponentMember["parameterDefinitions"]>[number], checked: boolean) => {
    if (!onBindingChange || !parameter) return;
    const existing = bindings.find(binding => binding.parameterId === parameter.id && binding.memberId === member.id && binding.memberParameterId === definition.id);
    const id = existing?.id || `${member.id}:${parameter.id}:${definition.id}`;
    onBindingChange(id, checked ? { id, parameterId: parameter.id, memberId: member.id, memberParameterId: definition.id } : null);
  };
  return <>
    <div className="parameter-intro">组件尺寸继承零部件参数和组件间构成规则。体积统一按立方米显示，重量统一按 kg 显示，均固定保留 3 位小数。</div>
    {(definitionName || definitionCode) && <section className="parameter-identity" aria-label="组件身份"><h4>组件信息</h4><div className="info-row"><span>名称<small>{definitionName || "未命名"}</small></span><span>编码<small>{definitionCode || "未设置"}</small></span></div></section>}
    <div className="parameter-controls">{parameters.length === 0 && <p className="empty-text">当前组件没有可调整参数。</p>}{parameters.map(parameter => isDerivedParameter(parameter) ? <div className="parameter-control" key={parameter.id}><div className="parameter-control-heading"><label>{parameter.label}<small>{parameter.unit}</small></label><output>{derivedMetric(parameter)?.value || "未配置"}</output></div><small className="parameter-range">由组件尺寸自动计算，不可直接编辑</small></div> : <ParameterControl key={parameter.id} parameter={parameter} bindings={bindings} patterns={patterns} members={members} onChange={onChange} />)}</div>
    <section className="parameter-metrics" aria-label="组件变化参数"><h4>组件变化参数</h4><MetricRow metric={metrics.length}/><MetricRow metric={metrics.width}/><MetricRow metric={metrics.height}/><MetricRow metric={metrics.volume}/><MetricRow metric={metrics.weight}/></section>
    <section className="parameter-members" aria-label="组件构成清单"><h4>组件构成（只读）</h4>{members.length === 0 && <p className="empty-text">暂无已放置零部件。</p>}{members.map(member => { const repeated = patterns.filter(pattern => pattern.memberIds.includes(member.id)).reduce((count, pattern) => count * Math.max(1, Math.round(parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1)), 1); return <div className="info-row" key={`composition-${member.id}`}><span>{member.name}<small>{member.code || member.role}</small></span><b>× {repeated}</b></div>; })}<p className="hint">组成清单仅用于说明。需要更改成员或接口连接时，请返回定义阶段。</p></section>
    <section className="parameter-binding-section"><h4>成员参数绑定</h4><p className="hint">默认根据零部件参数名称推断总长、总宽、总高的来源；可勾选或取消成员参数进行调整。</p>{(["length", "width", "height"] as const).map(axis => { const parameter = parameters.find(item => isDimension(item, axis)); const candidates = parameter ? dimensionCandidates(parameter, axis, members) : []; return <div className="dimension-binding-group" key={axis}><strong>{axis === "length" ? "组件总长" : axis === "width" ? "组件总宽" : "组件总高"}</strong>{!parameter && <small>未找到对应组件参数</small>}{parameter && candidates.length === 0 && <small>暂无名称匹配的成员尺寸参数</small>}{candidates.map(({ member, definition }) => { const selected = bindings.some(binding => binding.parameterId === parameter?.id && binding.memberId === member.id && binding.memberParameterId === definition.id); return <label key={`${member.id}:${definition.id}`}><input type="checkbox" checked={selected} onChange={event => toggleDimensionBinding(parameter, member, definition, event.target.checked)}/>{member.name}.{definition.label}{selected ? "（已绑定）" : "（未选）"}</label>; })}</div>; })}</section>
    {bindings.length === 0 && <p className="empty-text">暂无成员尺寸绑定。</p>}
    {parameters.map(parameter => <div className="info-row" key={`parameter-action-${parameter.id}`}><span>{parameter.label}<small>{parameter.id}</small></span>{onRemoveParameter && !isDerivedParameter(parameter) && <button type="button" className="ghost" onClick={() => onRemoveParameter(parameter.id)}>删除参数</button>}</div>)}
    {bindings.filter(binding => { const parameter = parameters.find(item => item.id === binding.parameterId); return !parameter || !( ["length", "width", "height"] as const).some(axis => isDimension(parameter, axis)); }).map(binding => <div className="info-row" key={binding.id}><span>{binding.parameterId}<small>→ {binding.memberId} / {binding.memberParameterId}</small></span>{onBindingChange && <button type="button" className="ghost" onClick={() => onBindingChange(binding.id, null)}>移除</button>}</div>)}
    {onAddParameter && <div className="parameter-add"><h4>新增组件可调参数</h4><p className="hint">保留此栏用于创建尺寸、数量、间距或其他会驱动成员与构成规则的参数；体积和重量属于派生指标，不需要在此重复创建。</p><div className="parameter-add-grid"><input placeholder="稳定 ID" value={newParameter.id} onChange={event => setNewParameter({ ...newParameter, id: event.target.value })}/><input placeholder="显示名称" value={newParameter.label} onChange={event => setNewParameter({ ...newParameter, label: event.target.value })}/><input placeholder="单位" value={newParameter.unit} onChange={event => setNewParameter({ ...newParameter, unit: event.target.value })}/><input type="number" placeholder="最小值" value={newParameter.min} onChange={event => setNewParameter({ ...newParameter, min: event.target.value })}/><input type="number" placeholder="最大值" value={newParameter.max} onChange={event => setNewParameter({ ...newParameter, max: event.target.value })}/><input type="number" placeholder="默认值" value={newParameter.value} onChange={event => setNewParameter({ ...newParameter, value: event.target.value })}/></div><button type="button" className="ghost" onClick={addParameter}>添加参数</button></div>}
    {patterns.length > 0 && <section className="parameter-add"><h4>成员阵列规则</h4>{patterns.map(pattern => <div className="info-row" key={pattern.id}><span>{pattern.label}<small>{pattern.memberIds.length} 个成员 · {pattern.axis} 轴 · {pattern.countParameterId} × {pattern.spacingParameterId}</small></span>{onRemovePattern && <button type="button" className="ghost" onClick={() => onRemovePattern(pattern.id)}>移除</button>}</div>)}</section>}
    {onAddPattern && members.length > 0 && <div className="parameter-add"><h4>新增成员阵列规则</h4><p className="hint">数量、间距和轴向用于重复放置成员；它与下方基于尺寸表达式的组件间构成规则分开保存。</p><div className="parameter-add-grid"><input placeholder="稳定 ID" value={newPattern.id} onChange={event => setNewPattern({ ...newPattern, id: event.target.value })}/><input placeholder="显示名称" value={newPattern.label} onChange={event => setNewPattern({ ...newPattern, label: event.target.value })}/><select value={newPattern.countParameterId} onChange={event => setNewPattern({ ...newPattern, countParameterId: event.target.value })}><option value="">数量参数</option>{parameters.filter(parameter => ["个", "件"].includes(parameter.unit)).map(parameter => <option key={parameter.id} value={parameter.id}>{parameter.label}</option>)}</select><select value={newPattern.spacingParameterId} onChange={event => setNewPattern({ ...newPattern, spacingParameterId: event.target.value })}><option value="">间距参数</option>{parameters.filter(parameter => parameter.unit === "mm").map(parameter => <option key={parameter.id} value={parameter.id}>{parameter.label}</option>)}</select><select value={newPattern.axis} onChange={event => setNewPattern({ ...newPattern, axis: event.target.value as ComponentPattern["axis"] })}><option value="x">X 轴</option><option value="y">Y 轴</option><option value="z">Z 轴</option></select></div><div className="pattern-members">{members.map(member => <label key={member.id}><input type="checkbox" checked={newPattern.memberIds.includes(member.id)} onChange={event => setNewPattern({ ...newPattern, memberIds: event.target.checked ? [...newPattern.memberIds, member.id] : newPattern.memberIds.filter(id => id !== member.id) })}/>{member.name}</label>)}</div><button type="button" className="ghost" onClick={addPattern}>添加阵列规则</button></div>}
    <div className="parameter-add"><h4>新增绑定：其他限制条件</h4><p className="hint">用于记录组件除成员尺寸绑定之外的自定义限制，例如安装间隙、连接方向或适用条件。当前保存为说明，不会自动执行。</p>{rules.filter(rule => (rule.kind || "constraint") === "constraint").map(rule => <div className="info-row" key={rule.id}><span>{rule.label || "其他限制条件"}<small>{rule.expression}</small></span>{onRemoveRule && rule.id !== "rule-1" && <button type="button" className="ghost" onClick={() => onRemoveRule(rule.id)}>移除</button>}</div>)}<div className="parameter-add-grid"><input placeholder="名称（可选）" value={newConstraint.label} onChange={event => setNewConstraint({ ...newConstraint, label: event.target.value })}/><textarea placeholder="限制条件说明" value={newConstraint.expression} onChange={event => setNewConstraint({ ...newConstraint, expression: event.target.value })}/></div><button type="button" className="ghost" onClick={() => addRule("constraint")}>添加限制条件</button></div>
    <div className="parameter-add"><h4>新增组件间构成规则</h4><p className="hint">默认为空。需要时可用组件总长、总宽、总高或自定义参数描述组件之间的构成变化，例如“宽度变化时，两组件间距保持为 20 mm”。当前规则先保存表达式，执行能力尚未接入。</p>{rules.filter(rule => rule.kind === "composition").map(rule => <div className="info-row" key={rule.id}><span>{rule.label || "组件间构成规则"}<small>{rule.expression}</small></span>{onRemoveRule && <button type="button" className="ghost" onClick={() => onRemoveRule(rule.id)}>移除</button>}</div>)}<div className="parameter-add-grid"><input placeholder="名称（可选）" value={newCompositionRule.label} onChange={event => setNewCompositionRule({ ...newCompositionRule, label: event.target.value })}/><textarea placeholder="基于长宽高或其他尺寸参数的构成规则" value={newCompositionRule.expression} onChange={event => setNewCompositionRule({ ...newCompositionRule, expression: event.target.value })}/></div><button type="button" className="ghost" onClick={() => addRule("composition")}>添加组件间构成规则</button></div>
    <div className="callout">组件参数会继承成员可调范围。若零部件未提供质量，或未同时提供实体体积和材料密度，重量会显示为未配置；包络体积不会被当作材料实体体积。</div>
  </>;
}
