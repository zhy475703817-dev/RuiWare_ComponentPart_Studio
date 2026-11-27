import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { HardwareItem, InterfaceHardware, PartInterface } from "../domain/component";
import { inferHardwareCategory } from "../domain/hardware";
import { assessInterfaceHardware, hardwareScenarios, HARDWARE_SCENARIOS } from "../domain/hardwareSelection";
import { HardwarePresetPicker } from "./HardwarePresetPicker";

export function InterfaceHardwareEditor({ memberId, item, editable, onSave }: { memberId: string; item: PartInterface; editable: boolean; onSave: (memberId: string, interfaceId: string, hardware: InterfaceHardware) => void }) {
  const storedKey = JSON.stringify(item.hardware || null);
  const [config, setConfig] = useState<InterfaceHardware>(item.hardware || { mode: "auto" });
  useEffect(() => { setConfig(item.hardware || { mode: "auto" }); }, [memberId, item.id, storedKey]);
  const items = config.items || [];
  const scenario = HARDWARE_SCENARIOS.find(entry => entry.id === config.scenarioId);
  const assessment = assessInterfaceHardware(item, config);
  const setItems = (next: HardwareItem[]) => setConfig(current => ({ ...current, items: next }));
  const patchItem = (index: number, patch: Partial<HardwareItem>) => setItems(items.map((entry, row) => row === index ? { ...entry, ...patch } : entry));
  const dirty = JSON.stringify(config) !== JSON.stringify(item.hardware || { mode: "auto" });
  return <div className="interface-hardware-editor" aria-label="接口五金配置">
    <label>连接场景<select aria-label={`${item.name}：五金连接场景`} disabled={!editable} value={config.scenarioId || (config.mode === "auto" ? "auto" : "")} onChange={event => {
      const next = HARDWARE_SCENARIOS.find(entry => entry.id === event.currentTarget.value);
      if (!next) { setConfig({ mode: "auto" }); return; }
      const currentItems = config.mode === "manual" ? items : [];
      const categories = new Set(currentItems.map(entry => entry.category?.trim() || inferHardwareCategory(entry.name)));
      const nextItems = [...currentItems, ...next.items.filter(entry => !categories.has(entry.category)).map(entry => ({ ...entry }))];
      if (next.mode === "manual" && !nextItems.length) nextItems.push({ category: "其他", name: "五金件", specification: "", quantity: 1 });
      setConfig({ mode: next.mode as InterfaceHardware["mode"], scenarioId: next.id, note: config.note || "", items: next.mode === "none" ? [] : nextItems.slice(0, 50) });
    }}>
      <option value="auto">自动参考 / 尚未确认</option>
      {!config.scenarioId && config.mode !== "auto" && <option value="">旧配置：请选择场景</option>}
      {hardwareScenarios(item).map(entry => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
    </select></label>
    <small>全部场景均可选；接口类型只影响排列顺序。尺寸仅为五金填写值，不作接口适用检查。</small>
    {scenario && <div className="hardware-scenario-note"><small>{scenario.condition}</small><small>清单类别：{scenario.requiredCategories.join("、") || "无需额外五金"}{scenario.optionalCategories.length ? `；按需添加：${scenario.optionalCategories.join("、")}` : ""}</small></div>}
    {config.mode === "auto" && <small>请选择实际连接场景后填写五金；平台不会根据接口尺寸自动决定规格。</small>}
    {config.mode === "manual" && <>
      {items.map((entry, index) => <section className="hardware-item-editor" key={index} aria-label={`第 ${index + 1} 行五金`}>
        <div className="hardware-edit-row">
          <label>类别<input aria-label={`${item.name}：五金类别 ${index + 1}`} maxLength={80} disabled={!editable} value={entry.category ?? inferHardwareCategory(entry.name)} placeholder="例如：螺栓" onChange={event => patchItem(index, { category: event.currentTarget.value })}/></label>
          <label>名称<input aria-label={`${item.name}：五金名称 ${index + 1}`} maxLength={80} disabled={!editable} value={entry.name} placeholder="例如：六角螺栓" onChange={event => patchItem(index, { name: event.currentTarget.value })}/></label>
          <label>规格<input aria-label={`${item.name}：五金规格 ${index + 1}`} maxLength={120} disabled={!editable} value={entry.specification} placeholder="例如：M8 × 30 mm；可自定义" onChange={event => patchItem(index, { specification: event.currentTarget.value })}/></label>
          <label>数量<input aria-label={`${item.name}：五金数量 ${index + 1}`} type="number" min={1} max={10000} step={1} disabled={!editable} value={entry.quantity} onChange={event => patchItem(index, { quantity: Math.min(10000, Math.max(1, Math.floor(event.currentTarget.valueAsNumber) || 1)) })}/></label>
          <button type="button" className="icon-button" aria-label={`删除第 ${index + 1} 类五金`} disabled={!editable} onClick={() => setItems(items.filter((_, row) => row !== index))}><Trash2 size={14}/></button>
        </div>
        <HardwarePresetPicker disabled={!editable} onApply={preset => patchItem(index, preset)}/>
      </section>)}
      <button type="button" className="ghost compact-action" disabled={!editable || items.length >= 50} onClick={() => setItems([...items, { category: "其他", name: "五金件", specification: "", quantity: 1 }])}><Plus size={13}/>增加五金</button>
    </>}
    {config.mode !== "auto" && <label>配置说明{scenario?.requiresNote ? "（必填）" : "（可选）"}<textarea aria-label={`${item.name}：五金配置说明`} maxLength={500} disabled={!editable} value={config.note || ""} placeholder="例如：直接焊接，无额外五金；或已由某成员提供" onChange={event => { const note = event.currentTarget.value; setConfig(current => ({ ...current, note })); }}/></label>}
    <div className={`hardware-selection-status ${assessment.status}`} role="status"><b>{assessment.status === "pending" ? "待填写 / 确认" : assessment.status === "configured" ? "已填写五金" : "已说明无需额外五金"}{dirty ? "（未保存）" : ""}</b>{assessment.issues.map((issue, index) => <small key={`${issue.code}-${index}`}>{issue.message}</small>)}</div>
    <button type="button" className="primary compact-action" disabled={!editable || !dirty || (config.mode === "manual" && !items.length) || items.some(entry => !entry.name.trim())} onClick={() => onSave(memberId, item.id, config)}>保存五金配置</button>
    <small>可保存未填写完整的配置。保存后自动更新统计；共用节点仅计一次，节点状态以汇总为准。</small>
  </div>;
}
