import { Download, Upload } from "lucide-react";
import type { ValidationCheck } from "../domain/validation";

export function PublishStage({ checks, revision, onExport, onPublish, publishing }: { checks: ValidationCheck[]; revision: number; onExport: () => void; onPublish: () => void; publishing: boolean }) {
  const failed = checks.filter(check => check.status === "fail").length;
  const pending = checks.filter(check => check.status === "pending").length;
  return <>
    <div className="callout"><b>当前组件草稿 R{revision}</b><br/>{failed} 项基础检查待处理，{pending} 项能力待接入。</div>
    <p className="empty-text">当前可先发布结构版本，保存成员引用和检查结果。发布后可在左侧找到，并加入其他组件；工程检查未完成的版本会保留提示。</p>
    <button className="primary full" onClick={onPublish} disabled={publishing}><Upload size={15}/>{publishing ? "正在发布…" : "发布组件版本"}</button>
    <button className="ghost full" onClick={onExport}><Download size={15}/>导出组件草稿</button>
  </>;
}
