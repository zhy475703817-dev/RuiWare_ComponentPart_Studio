import type { ComponentMember, ComponentParameter, ParameterBinding, ComponentPattern } from "./component.ts";

export type Metric = { label: string; value: string; note?: string };

const aliases = {
  length: ["componentLength", "length", "overallLength", "总长", "长度"],
  width: ["componentWidth", "width", "overallWidth", "总宽", "宽度"],
  height: ["componentHeight", "height", "overallHeight", "总高", "高度"],
  volume: ["componentVolume", "volume", "overallVolume", "体积"],
  weight: ["componentWeight", "weight", "mass", "重量", "质量"],
};

const densityAliases = ["density", "materialDensity", "密度", "材料密度"];
const partMassAliases = ["weight", "mass", "重量", "质量"];
const partVolumeAliases = ["solidVolume", "materialVolume", "partVolume", "零件实体体积", "材料体积"];

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

function formatFixed3(value: number, unit = ""): string {
  if (!Number.isFinite(value)) return "不可用";
  return `${value.toLocaleString("zh-CN", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}${unit}`;
}

function numericMemberValue(member: ComponentMember, keys: string[]): number | undefined {
  const direct = Object.entries(member.parameterValues).find(([id, value]) => keys.some(key => id.toLowerCase() === key.toLowerCase()) && typeof value === "number" && Number.isFinite(value));
  if (direct && typeof direct[1] === "number") return direct[1];
  const definition = (member.parameterDefinitions || []).find(item => keys.some(key => item.id.toLowerCase() === key.toLowerCase() || item.label.toLowerCase() === key.toLowerCase()));
  if (!definition) return undefined;
  const value = member.parameterValues[definition.id];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return typeof definition.default === "number" && Number.isFinite(definition.default) ? definition.default : undefined;
}

function massInKilograms(member: ComponentMember): number | undefined {
  const massDefinition = (member.parameterDefinitions || []).find(item => partMassAliases.some(key => item.id.toLowerCase() === key || item.label.toLowerCase() === key));
  if (massDefinition) {
    const value = numericMemberValue(member, [massDefinition.id]);
    if (value === undefined) return undefined;
    const unit = massDefinition.unit.trim().toLowerCase();
    if (["g", "gram", "grams", "克"].includes(unit)) return value / 1000;
    if (["kg", "千克"].includes(unit)) return value;
    return undefined;
  }
  const volumeDefinition = (member.parameterDefinitions || []).find(item => partVolumeAliases.some(key => item.id.toLowerCase() === key || item.label.toLowerCase() === key));
  const densityDefinition = (member.parameterDefinitions || []).find(item => densityAliases.some(key => item.id.toLowerCase() === key || item.label.toLowerCase() === key));
  if (!volumeDefinition || !densityDefinition) return undefined;
  const volume = numericMemberValue(member, [volumeDefinition.id]);
  const density = numericMemberValue(member, [densityDefinition.id]);
  if (volume === undefined || density === undefined) return undefined;
  const volumeUnit = volumeDefinition.unit.trim().toLowerCase();
  const volumeScale = ["mm3", "mm³", "毫米³", "立方毫米"].includes(volumeUnit) ? 1e-9
    : ["cm3", "cm³", "厘米³", "立方厘米"].includes(volumeUnit) ? 1e-6
      : ["m3", "m³", "立方米"].includes(volumeUnit) ? 1 : undefined;
  const densityUnit = densityDefinition.unit.trim().toLowerCase();
  const densityScale = ["kg/m3", "kg/m³", "千克/立方米"].includes(densityUnit) ? 1
    : ["g/cm3", "g/cm³", "克/立方厘米"].includes(densityUnit) ? 1000
      : ["kg/mm3", "kg/mm³", "千克/立方毫米"].includes(densityUnit) ? 1e9 : undefined;
  return volumeScale && densityScale ? volume * volumeScale * density * densityScale : undefined;
}

function densityInKgPerCubicMetre(value: number | undefined, unit: string | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  const normalized = unit?.trim().toLowerCase();
  if (["kg/m3", "kg/m³", "千克/立方米"].includes(normalized || "")) return value;
  if (["g/cm3", "g/cm³", "克/立方厘米"].includes(normalized || "")) return value * 1000;
  if (["kg/mm3", "kg/mm³", "千克/立方毫米"].includes(normalized || "")) return value * 1e9;
  return undefined;
}

function memberExtent(member: ComponentMember, axisIndex: number): { start: number; end: number } | undefined {
  const dimensions = [aliases.length, aliases.width, aliases.height].map(dimension => numericMemberValue(member, dimension));
  // A part may expose only one or two envelope dimensions. Use the available
  // axis dimension and keep missing axes at zero so an initial component
  // envelope can still be shown without inventing a size.
  if (dimensions[axisIndex] === undefined) return undefined;
  const half = dimensions.map(dimension => typeof dimension === "number" ? Math.max(0, dimension) / 2 : 0);
  const [x, y, z] = member.rotation;
  const a = Math.cos(x), b = Math.sin(x), c = Math.cos(y), d = Math.sin(y), e = Math.cos(z), f = Math.sin(z);
  const rotation = [
    [c * e, -c * f, d],
    [a * f + b * d * e, -a * e + b * d * f, -b * c],
    [b * f - a * d * e, b * e + a * d * f, a * c],
  ];
  const radius = rotation[axisIndex].reduce((sum, factor, index) => sum + Math.abs(factor) * half[index], 0);
  const center = Number(member.position[axisIndex]);
  return Number.isFinite(center) ? { start: center - radius, end: center + radius } : undefined;
}

function expandMembers(parameters: ComponentParameter[], members: ComponentMember[], patterns: ComponentPattern[]): ComponentMember[] {
  return members.flatMap(member => {
    const memberPatterns = patterns.filter(pattern => pattern.memberIds.includes(member.id));
    if (!memberPatterns.length) return [member];
    const copies: ComponentMember[] = [member];
    for (const pattern of memberPatterns) {
      const count = Math.max(1, Math.round(parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1));
      const spacing = Number(parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0);
      for (let index = 1; index < count; index += 1) {
        const position: ComponentMember["position"] = [...member.position];
        const axis = pattern.axis === "x" ? 0 : pattern.axis === "y" ? 1 : 2;
        position[axis] += Number.isFinite(spacing) ? spacing * index : 0;
        copies.push({ ...member, position });
      }
    }
    return copies;
  });
}

function inferDimension(parameters: ComponentParameter[], keys: string[], members: ComponentMember[], patterns: ComponentPattern[] = []): ComponentParameter | undefined {
  const explicit = findParameter(parameters, keys);
  if (explicit && explicit.value !== null && Number.isFinite(explicit.value)) return explicit;
  const expanded = expandMembers(parameters, members, patterns);
  const axis = keys === aliases.length ? 0 : keys === aliases.width ? 1 : 2;
  const inferredExtents = expanded.map(member => memberExtent(member, axis)).filter((item): item is { start: number; end: number } => Boolean(item));
  if (!inferredExtents.length) return findParameter(parameters, keys);
  const min = Math.min(...inferredExtents.map(item => item.start));
  const max = Math.max(...inferredExtents.map(item => item.end));
  const value = Math.max(0, max - min);
  return { id: `derived-${keys[0]}`, label: keys === aliases.length ? "推算组件长度" : keys === aliases.width ? "推算组件宽度" : "推算组件高度", value, unit: "mm", min: value, max: value };
}

/** Return the current assembly envelope in millimetres, independent of the
 * editable component dimension parameter values. This is used to seed the
 * initial dimensions after members are placed. */
export function inferEnvelopeDimensions(parameters: ComponentParameter[], members: ComponentMember[], patterns: ComponentPattern[] = []): { length?: number; width?: number; height?: number } {
  const expanded = expandMembers(parameters, members, patterns);
  const dimensions: Array<number | undefined> = [0, 1, 2].map(axis => {
    const extents = expanded.map(member => memberExtent(member, axis)).filter((item): item is { start: number; end: number } => Boolean(item));
    if (!extents.length) return undefined;
    return Math.max(0, Math.max(...extents.map(item => item.end)) - Math.min(...extents.map(item => item.start)));
  });
  return { length: dimensions[0], width: dimensions[1], height: dimensions[2] };
}

function parameterMetric(parameters: ComponentParameter[], keys: string[], label: string, bindings: ParameterBinding[], members: ComponentMember[], patterns: ComponentPattern[] = [], missingNote = "需要有效的组件参数值"): Metric {
  const isDimension = keys === aliases.length || keys === aliases.width || keys === aliases.height;
  const parameter = isDimension ? inferDimension(parameters, keys, members, patterns) || findParameter(parameters, keys) : findParameter(parameters, keys);
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

export function deriveComponentMetrics(parameters: ComponentParameter[], bindings: ParameterBinding[] = [], members: ComponentMember[] = [], patterns: ComponentPattern[] = []) {
  const length = inferDimension(parameters, aliases.length, members, patterns);
  const width = inferDimension(parameters, aliases.width, members, patterns);
  const height = inferDimension(parameters, aliases.height, members, patterns);
  const expandedMembers = expandMembers(parameters, members, patterns);
  const computedVolume = [length?.value, width?.value, height?.value].every(value => typeof value === "number" && Number.isFinite(value))
    ? (length!.value as number) * (width!.value as number) * (height!.value as number)
    : undefined;
  const partMasses = expandedMembers.map(member => {
    return massInKilograms(member);
  });
  const computedPartMass = partMasses.length > 0 && partMasses.every(value => value !== undefined) ? partMasses.reduce((sum, value) => sum + (value || 0), 0) : undefined;
  return {
    length: parameterMetric(parameters, aliases.length, "长度参数", bindings, members, patterns),
    width: parameterMetric(parameters, aliases.width, "宽度参数", bindings, members, patterns),
    height: parameterMetric(parameters, aliases.height, "高度参数", bindings, members, patterns),
    volume: computedVolume !== undefined && members.length > 0 ? { label: "包络体积", value: formatFixed3(computedVolume / 1_000_000_000, " m³"), note: "根据组件总长 × 总宽 × 总高换算为立方米；表示外形包络，不代表材料实体体积" } : { label: "包络体积", value: "未配置", note: "需要已放置成员及组件长、宽、高参数才能自动推算" },
    weight: computedPartMass !== undefined ? { label: "重量 / 质量参数", value: formatFixed3(computedPartMass, " kg"), note: "根据组件成员重量或零件实体体积与材料密度汇总" } : { label: "重量 / 质量参数", value: "未配置", note: "需要零部件重量，或零件实体体积与材料密度；包络体积不用于估算重量" },
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
