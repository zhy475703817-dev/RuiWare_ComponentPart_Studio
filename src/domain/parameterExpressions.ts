import type { ComponentMember, ComponentParameter, ComponentPattern, ParameterBinding } from "./component.ts";

export type ParameterExpression = { id: string; expression: string; note: string };

const parameterName = (parameter: ComponentParameter) => `${parameter.label}（${parameter.id}）`;

/** Describe the bindings and array formulas actually used by composition resolution. */
export function parameterExpressions(parameter: ComponentParameter, parameters: ComponentParameter[], bindings: ParameterBinding[], members: ComponentMember[], patterns: ComponentPattern[]): ParameterExpression[] {
  const key = `${parameter.id} ${parameter.label}`.toLowerCase();
  if (["volume", "体积"].some(token => key.includes(token))) return [{
    id: "derived-volume", expression: "包络体积（m³） = 组件长度（mm） × 组件宽度（mm） × 组件高度（mm） / 10⁹",
    note: "尺寸优先取组件尺寸参数，未设置时由成员包络推算；需要已放置成员及完整的长、宽、高。",
  }];
  if (["weight", "mass", "重量", "质量"].some(token => key.includes(token))) return [{
    id: "derived-weight", expression: "组件重量（kg） = Σ 成员实例重量（kg）",
    note: "包含阵列实例；成员重量取零件质量，或实体体积 × 材料密度，并统一换算为 kg。",
  }];

  const expressions: ParameterExpression[] = [];
  for (const binding of bindings.filter(item => item.parameterId === parameter.id)) {
    const member = members.find(item => item.id === binding.memberId);
    const definition = member?.parameterDefinitions?.find(item => item.id === binding.memberParameterId);
    if (!member || !definition || definition.unit !== parameter.unit || !["number", "integer"].includes(definition.valueType)) continue;
    const min = Math.max(parameter.min, definition.minimum ?? -Infinity);
    const max = Math.min(parameter.max, definition.maximum ?? Infinity);
    if (min > max) continue;
    const source = parameterName(parameter);
    const rounded = definition.valueType === "integer" ? `round(${source})` : source;
    const current = parameter.value === null ? member.parameterValues[definition.id] ?? definition.default
      : Math.min(max, Math.max(min, definition.valueType === "integer" ? Math.round(parameter.value) : parameter.value));
    expressions.push({
      id: `binding-${binding.id}`,
      expression: `${member.name} · ${definition.label}（${definition.id}） = clamp(${rounded}, ${min}, ${max})`,
      note: `成员：${member.id}；当前：${current}${definition.unit ? ` ${definition.unit}` : ""}。clamp 表示限制到范围${definition.valueType === "integer" ? "，round 表示四舍五入" : ""}；组件参数未设置时保留成员自身数值或模板默认值。`,
    });
  }
  for (const pattern of patterns.filter(item => item.countParameterId === parameter.id || item.spacingParameterId === parameter.id)) {
    const count = parameters.find(item => item.id === pattern.countParameterId);
    const spacing = parameters.find(item => item.id === pattern.spacingParameterId);
    const sources = members.filter(member => pattern.memberIds.includes(member.id));
    if (!count || !spacing || !sources.length) continue;
    const repetitions = Math.max(1, Math.round(count.value ?? 1));
    const offset = spacing.value ?? 0;
    expressions.push({
      id: `pattern-count-${pattern.id}`,
      expression: `${pattern.label} · 每个成员的实例数 N = max(1, round(${parameterName(count)}))`,
      note: `当前 N = ${repetitions}；数量未设置时按 1，round 表示四舍五入。`,
    }, {
      id: `pattern-position-${pattern.id}`,
      expression: `${pattern.label} · 成员第 i 个实例的 ${pattern.axis.toUpperCase()} 坐标 = 原始 ${pattern.axis.toUpperCase()} 坐标 + i × ${parameterName(spacing)}`,
      note: `i = 0…N−1；当前间距 = ${offset} mm（未设置时按 0）。适用成员：${sources.map(member => `${member.name}（${member.id}）`).join("、")}。`,
    });
  }
  return expressions;
}
