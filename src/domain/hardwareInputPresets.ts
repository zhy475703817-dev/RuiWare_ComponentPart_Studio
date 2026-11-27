import catalog from "./hardwareInputPresets.json" with { type: "json" };
import type { HardwareItem } from "./component.ts";

export const HARDWARE_INPUT_PRESETS = catalog;
export type HardwarePresetFamily = typeof catalog.families[number];

/** Input assistance only: deliberately has no interface or geometry argument. */
export function formatHardwarePreset(familyId: string, values: Record<string, string>): Omit<HardwareItem, "quantity"> {
  const family = catalog.families.find(entry => entry.id === familyId);
  if (!family) throw new Error("请选择五金预设类型。");
  for (const field of family.fields) {
    if (typeof values[field.id] !== "string" || !values[field.id].trim()) throw new Error(`请填写${field.label}。`);
  }
  const specification = family.format.replace(/\{(\w+)\}/g, (_, key: string) => values[key].trim());
  if (specification.length > 120) throw new Error("规格文字不能超过 120 字符。");
  return { category: family.category, name: family.name, specification };
}
