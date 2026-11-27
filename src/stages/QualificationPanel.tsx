import { useEffect, useState } from "react";
import type { useQualification } from "../app/useQualification";
import type { Vector3 } from "../domain/component";

export function QualificationPanel({ qualification }: { qualification: ReturnType<typeof useQualification> }) {
  const { report, busy, error } = qualification;
  const [automatic, setAutomatic] = useState(true), [axes, setAxes] = useState<number[]>([]);
  const [minimum, setMinimum] = useState(["", "", ""]), [maximum, setMaximum] = useState(["", "", ""]);
  const [targets, setTargets] = useState(""), [formError, setFormError] = useState("");
  const configKey = JSON.stringify(report?.config);
  useEffect(() => {
    if (!report) return;
    setAutomatic(report.config.axes === null); setAxes(report.config.axes || []);
    setMinimum(report.config.minimumDimensions?.map(String) || ["", "", ""]);
    setMaximum(report.config.maximumDimensions?.map(String) || ["", "", ""]);
    setTargets(report.config.targets.map(v => v.join(", ")).join("\n"));
  }, [configKey]);
  function save() {
    const specified = [...minimum, ...maximum].some(v => v.trim());
    const low = minimum.map(Number), high = maximum.map(Number);
    const points = targets.trim() ? targets.trim().split(/\n+/).map(line => line.trim().split(/[,，\s]+/).map(Number)) : [];
    if (specified && [...minimum, ...maximum].some(v => !v.trim() || !Number.isFinite(Number(v)) || Number(v) <= 0)
        || specified && low.some((v, i) => v > high[i]) || points.length > 8
        || points.some(v => v.length !== 3 || v.some(n => !Number.isFinite(n) || n <= 0))) {
      setFormError("请完整填写正数的最小/最大尺寸；额外测试每行填写 X、Y、Z，最多8组。"); return;
    }
    if (specified && points.some(point => point.some((v, i) => v < low[i] || v > high[i]))) {
      setFormError("额外测试尺寸必须位于声明的使用范围内。"); return;
    }
    setFormError("");
    void qualification.configure({ axes: automatic ? null : axes, minimumDimensions: specified ? low as Vector3 : null,
      maximumDimensions: specified ? high as Vector3 : null, targets: points as Vector3[] });
  }
  const passed = report?.cases.filter(c => c.status === "passed").length || 0;
  const failed = report?.cases.filter(c => c.status === "failed").length || 0;
  return <section className="geometry-resize" aria-label="自动制作验收">
    <h4>自动制作验收</h4>
    <p role="status">{report?.message || "保存后自动检查原尺寸和尺寸联动；通过后才能发布。"}</p>
    {busy && report?.detail && <small>{report.detail}</small>}
    {report && <p>{qualification.deliveryReady ? "当前修订已通过" : "当前修订尚未通过"} · {passed}/{report.cases.length} 项通过{failed ? `，${failed} 项需处理` : ""}</p>}
    {report?.axes && <p className="hint">检查方向：{report.axes.map(a => "XYZ"[a]).join("、") || "固定尺寸"}。试调不会保存测试尺寸。</p>}
    {report?.cases.length ? <details><summary>查看试调结果与失败原因</summary>{report.cases.map((c, i) => <p key={i}>
      {c.label} · {c.dimensions.map(v => Number(v.toFixed(3))).join(" × ")} mm · {{ passed: "通过", failed: "未通过", running: "计算中", pending: "等待" }[c.status] || c.status}
      {c.attempts && c.attempts > 1 ? "（分步修复后通过）" : ""}{c.error && <small>{c.error.message}</small>}
    </p>)}</details> : null}
    {report?.error?.details.map((d, i) => d.message && <p className="hint" key={i}>{d.message}</p>)}
    {report?.scope && <p className="hint">{report.scope}</p>}
    <details><summary>设置需要支持的使用范围</summary>
      <p className="hint">默认按参数绑定识别方向，测试增大、缩小约10%及组合尺寸；按孔距补齐撑杆的柱片使用可完整分格的高度。指定范围后，会验证范围边界，并在调整超出范围时提前提示。</p>
      <label><input type="checkbox" checked={automatic} onChange={e => setAutomatic(e.target.checked)}/>自动识别调整方向</label>
      {!automatic && <div>{[0, 1, 2].map(axis => <label key={axis}><input type="checkbox" checked={axes.includes(axis)} onChange={e => setAxes(e.target.checked ? [...axes, axis].sort() : axes.filter(a => a !== axis))}/>{"XYZ"[axis]}</label>)}<small>不选择方向表示固定尺寸；填写的尺寸范围仍会完整测试。</small></div>}
      {[0, 1, 2].map(axis => <div className="resize-dimensions" key={axis}>
        <label>{"XYZ"[axis]} 最小值<input type="number" min="0.001" step="any" value={minimum[axis]} onChange={e => setMinimum(minimum.map((v, i) => i === axis ? e.target.value : v))}/></label>
        <label>{"XYZ"[axis]} 最大值<input type="number" min="0.001" step="any" value={maximum[axis]} onChange={e => setMaximum(maximum.map((v, i) => i === axis ? e.target.value : v))}/></label>
      </div>)}
      <label>额外测试尺寸（每行 X, Y, Z，单位mm）<textarea value={targets} rows={3} onChange={e => setTargets(e.target.value)}/></label>
      {formError && <p role="alert">{formError}</p>}
      <button className="ghost" type="button" onClick={save}>保存范围并自动验收</button>
    </details>
    {error && <p role="alert">{error}</p>}
    <div className="resize-actions"><button className="ghost" type="button" disabled={busy} onClick={() => void qualification.start(true)}>重新验收并尝试修复</button>
      {busy && <button className="ghost" type="button" onClick={() => void qualification.cancel()}>暂停本次验收</button>}</div>
  </section>;
}
