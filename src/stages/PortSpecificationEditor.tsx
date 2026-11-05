import type { PortSpecification } from "../domain/component";

export const emptySpecification = (): PortSpecification => ({ category: "", role: "unknown", contact: "unknown", clearanceMinMm: null, clearanceMaxMm: null, dimensions: [] });
const optionalNumber = (value: string) => value === "" ? null : Number(value);

export function PortSpecificationEditor({ value, onChange }: { value: PortSpecification; onChange: (value: PortSpecification) => void }) {
  const patch = (fields: Partial<PortSpecification>) => onChange({ ...value, ...fields });
  return <fieldset className="port-specification"><legend>结构化配合规格</legend>
    <p className="hint">尺寸和公差可留空，表示未确定。两侧使用相同尺寸标识；公差为 ± 值、间隙为容纳侧尺寸减插入侧尺寸。</p>
    <label>接口类别 / 规格族<input value={value.category} placeholder="例如 beam-column-hook-v1" onChange={event => patch({ category: event.target.value })}/></label>
    <div className="port-two-columns"><label>配合角色<select value={value.role} onChange={event => patch({ role: event.target.value as PortSpecification["role"] })}><option value="unknown">未确定</option><option value="male">插入侧 / 凸端</option><option value="female">容纳侧 / 凹端</option><option value="symmetric">对称配合</option></select></label>
      <label>接触方式<select value={value.contact} onChange={event => patch({ contact: event.target.value as PortSpecification["contact"] })}><option value="unknown">未确定</option><option value="clearance">间隙配合</option><option value="contact">接触配合</option><option value="press-fit">压配合（允许负间隙）</option></select></label></div>
    <div className="port-two-columns"><label>允许间隙下限 · mm<input type="number" step="any" value={value.clearanceMinMm ?? ""} placeholder="未确定" onChange={event => patch({ clearanceMinMm: optionalNumber(event.target.value) })}/></label><label>允许间隙上限 · mm<input type="number" step="any" value={value.clearanceMaxMm ?? ""} placeholder="未确定" onChange={event => patch({ clearanceMaxMm: optionalNumber(event.target.value) })}/></label></div>
    {value.dimensions.map((dimension, index) => {
      const update = (fields: Partial<typeof dimension>) => patch({ dimensions: value.dimensions.map((item, i) => i === index ? { ...item, ...fields } : item) });
      return <fieldset className="port-dimension" key={index}><legend>关键尺寸 {index + 1}</legend>
        <label>尺寸标识<input required maxLength={80} value={dimension.key} placeholder="例如 slotWidth / pinDiameter" onChange={event => update({ key: event.target.value })}/></label>
        <div className="port-two-columns"><label>尺寸值<input type="number" min="0" step="any" value={dimension.value ?? ""} placeholder="未确定" onChange={event => update({ value: optionalNumber(event.target.value) })}/></label><label>单位<select value={dimension.unit} onChange={event => {
          const unit = event.target.value as typeof dimension.unit;
          const factors = { mm: 1, cm: 10, m: 1000 };
          const factor = factors[dimension.unit] / factors[unit];
          update({ unit, value: dimension.value === null ? null : dimension.value * factor, tolerance: dimension.tolerance === null ? null : dimension.tolerance * factor });
        }}><option value="mm">mm</option><option value="cm">cm</option><option value="m">m</option></select></label></div>
        <label>公差 ± · {dimension.unit}<input type="number" min="0" step="any" value={dimension.tolerance ?? ""} placeholder="未确定；明确无公差可填 0" onChange={event => update({ tolerance: optionalNumber(event.target.value) })}/></label>
        <button type="button" className="ghost" onClick={() => patch({ dimensions: value.dimensions.filter((_, i) => i !== index) })}>移除此尺寸</button>
      </fieldset>;
    })}
    <button type="button" className="ghost" disabled={value.dimensions.length >= 32} onClick={() => patch({ dimensions: [...value.dimensions, { key: "", value: null, unit: "mm", tolerance: null }] })}>＋ 添加关键尺寸</button>
  </fieldset>;
}
