import { AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import type { ValidationCheck } from "../domain/validation";

export function ValidationStage({ checks, revision, onValidate }: { checks: ValidationCheck[]; revision: number; onValidate: () => void }) {
  return <>
    <div className="callout">以下结果对应当前草稿 R{revision}。待接入项目不计为通过。</div>
    <div className="validation-list">{checks.map(check => <div key={check.id} className={check.status}>
      {check.status === "pass" ? <CheckCircle2 size={15}/> : check.status === "pending" ? <Clock3 size={15}/> : <AlertTriangle size={15}/>}
      <span>{check.label}<small>{check.status === "pending" ? "待接入 · " : check.status === "fail" ? "需要处理 · " : ""}{check.message}</small></span>
    </div>)}</div>
    <button className="primary full" onClick={onValidate}>重新验证基础数据</button>
  </>;
}
