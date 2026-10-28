import type { ComponentMember, ComponentParameter, ParameterBinding } from "./component.ts";

export type Metric = { label: string; value: string; note?: string };

const aliases = {
  length: ["componentLength", "length", "overallLength", "总长", "长度"],
  width: ["componentWidth", "width", "overallWidth", "总宽", "宽度"],
  height: ["componentHeight", "height", "overallHeight", "总高", "高度"],
  volume: ["componentVolume", "volume", "overallVolume", "体积"],
  weight: ["componentWeight", "weight", "mass", "重量", "质量"],
};

function findParameter(parameters: ComponentParameter[], keys: string[]): ComponentParameter | undefined {
  return parameters.find(parameter => {
    const id = parameter.id.trim().toLowerCase();
    const label = parameter.label.trim().toLowerCase();
    return keys.some(key => id === key.toLowerCase() || label === key.toLowerCase());
  });
}

function formatNumber(value: number, unit = ""): string {
  if (!Number.isFinite(value)) return "不可用";
  const digits = Math.abs(value) >= 100 ? 0 : 2;
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: digits })}${unit}`;
}

function parameterMetric(parameters: ComponentParameter[], keys: string[], label: string, bindings: ParameterBinding[], members: ComponentMember[], missingNote = "需要有效的组件参数值"): Metric {
  const parameter = findParameter(parameters, keys);
  if (!parameter || parameter.value === null || !Number.isFinite(parameter.value)) {
    return { label, value: "未配置", note: missingNote };
  }
  const bounds = effectiveParameterBounds(parameter, bindings, members);
  return {
    label,
    value: formatNumber(parameter.value, ` ${parameter.unit}`),
    note: `${formatNumber(bounds.min, ` ${parameter.unit}`)} ～ ${formatNumber(bounds.max, ` ${parameter.unit}`)}${bounds.sources.length ? `；继承自 ${bounds.sources.join("、")}` : "；组件参数范围"}`,
  };
}

export function deriveComponentMetrics(parameters: ComponentParameter[], bindings: ParameterBinding[] = [], members: ComponentMember[] = []) {
  return {
    length: parameterMetric(parameters, aliases.length, "长度参数", bindings, members),
    width: parameterMetric(parameters, aliases.width, "宽度参数", bindings, members),
    height: parameterMetric(parameters, aliases.height, "高度参数", bindings, members),
    volume: parameterMetric(parameters, aliases.volume, "体积参数", bindings, members, "未配置体积参数；无法仅凭长、宽、高可靠推算"),
    weight: parameterMetric(parameters, aliases.weight, "重量 / 质量参数", bindings, members, "未配置重量参数；可靠计算还需要实体体积和材料密度"),
  };
}

export function parameterRange(parameter: ComponentParameter | undefined): string | undefined {
  if (!parameter || !Number.isFinite(parameter.min) || !Number.isFinite(parameter.max)) return undefined;
  return `${formatNumber(parameter.min, ` ${parameter.unit}`)} ～ ${formatNumber(parameter.max, ` ${parameter.unit}`)}`;
}

export function effectiveParameterBounds(parameter: ComponentParameter, bindings: ParameterBinding[], members: ComponentMember[]): { min: number; max: number; sources: string[] } {
  let min = parameter.min;
  let max = parameter.max;
  const sources: string[] = [];
  for (const binding of bindings.filter(item => item.parameterId === parameter.id)) {
    const member = members.find(item => item.id === binding.memberId);
    const definition = member?.parameterDefinitions?.find(item => item.id === binding.memberParameterId);
    if (!definition || definition.unit !== parameter.unit || !["number", "integer"].includes(definition.valueType)) continue;
    if (typeof definition.minimum === "number") min = Math.max(min, definition.minimum);
    if (typeof definition.maximum === "number") max = Math.min(max, definition.maximum);
    sources.push(`${member?.name || binding.memberId}.${definition.label}`);
  }
  if (bindings.some(binding => binding.parameterId === parameter.id && members.some(member => member.id === binding.memberId && member.parameterDefinitions?.some(definition => definition.id === binding.memberParameterId && definition.unit === parameter.unit && definition.valueType === "integer")))) {
    min = Math.ceil(min);
    max = Math.floor(max);
  }
  return { min, max, sources };
}

export function formatParameterValue(parameter: ComponentParameter): string {
  return parameter.value === null ? "未设置" : formatNumber(parameter.value, ` ${parameter.unit}`);
}
