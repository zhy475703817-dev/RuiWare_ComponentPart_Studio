import type { ComponentParameter } from "./component.ts";

const presetDefaults: Record<string, number> = { componentLength: 2400, componentWidth: 600, componentHeight: 1800 };

export function parameterDefault(parameter: ComponentParameter): number | null {
  if (parameter.default !== undefined) return parameter.default;
  return presetDefaults[parameter.id] ?? parameter.value;
}
