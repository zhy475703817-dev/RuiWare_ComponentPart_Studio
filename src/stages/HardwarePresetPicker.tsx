import { useId, useState } from "react";
import type { HardwareItem } from "../domain/component";
import { formatHardwarePreset, HARDWARE_INPUT_PRESETS } from "../domain/hardwareInputPresets";

export function HardwarePresetPicker({ disabled, onApply }: { disabled: boolean; onApply: (value: Omit<HardwareItem, "quantity">) => void }) {
  const id = useId();
  const [familyId, setFamilyId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const family = HARDWARE_INPUT_PRESETS.families.find(entry => entry.id === familyId);
  let preset: Omit<HardwareItem, "quantity"> | undefined;
  let message = "";
  if (family) {
    try { preset = formatHardwarePreset(family.id, values); }
    catch (error) { message = error instanceof Error ? error.message : "请填写预设字段。"; }
  }
  return <details className="hardware-preset-picker">
    <summary>从常用规格填写</summary>
    <label>五金类型<select aria-label="五金规格预设类型" disabled={disabled} value={familyId} onChange={event => {
      const next = HARDWARE_INPUT_PRESETS.families.find(entry => entry.id === event.currentTarget.value);
      setFamilyId(next?.id || "");
      setValues(Object.fromEntries(next?.fields.map(field => [field.id, field.values[0] || ""]) || []));
    }}><option value="">选择预设类型</option>{HARDWARE_INPUT_PRESETS.families.map(entry => <option key={entry.id} value={entry.id}>{entry.name} · {entry.standard}</option>)}</select></label>
    {family && <>
      <div className="hardware-preset-fields">{family.fields.map(field => <label key={field.id}>{field.label}<input disabled={disabled} aria-label={`预设：${field.label}`} list={`${id}-${field.id}`} value={values[field.id] || ""} maxLength={120} onChange={event => { const value = event.currentTarget.value; setValues(current => ({ ...current, [field.id]: value })); }}/><datalist id={`${id}-${field.id}`}>{field.values.map(value => <option key={value} value={value}/>)}</datalist></label>)}</div>
      <small role="status">{preset?.specification || message}</small>
      <button type="button" className="ghost compact-action" disabled={disabled || !preset} onClick={() => { if (preset) onApply(preset); }}>填入此行（保留数量）</button>
      <a className="hardware-source" href={family.source} target="_blank" rel="noreferrer">查看类型来源</a>
    </>}
    <small>预设仅帮助填写尺寸/型号，可直接输入其他值；不读取接口尺寸、不检查适用性。组合不代表厂家库存型号。</small>
  </details>;
}
