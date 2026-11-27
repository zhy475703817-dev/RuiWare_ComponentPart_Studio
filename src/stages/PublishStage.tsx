import { Download, Upload } from "lucide-react";
import type { ValidationCheck } from "../domain/validation";

export function PublishStage({ checks, revision, onExport, onPublish, publishing, deliveryReady }: { checks: ValidationCheck[]; revision: number; onExport: () => void; onPublish: () => void; publishing: boolean; deliveryReady: boolean }) {
  const failed = checks.filter(check => check.status === "fail").length;
  const pending = checks.filter(check => check.status === "pending").length;
  const hardwareReady = !checks.some(check => check.id === "hardware.selection" && check.status !== "pass");
  return <>
    <div className="callout"><b>当前组件草稿 R{revision}</b><br/>{failed} 项基础检查待处理，{pending} 项能力待接入。</div>
    <p className="empty-text">当前修订通过自动制作验收后才能发布。发布包保存真实联动结果和试调记录；工程规则检查状态另行保留。</p>
    {!hardwareReady && <p className="callout">已连接接口需要五金时必须设置，未完成不能发布。请到接口阶段补齐五金，或明确无需五金的原因；不检查尺寸适用性。</p>}
    <button className="primary full" onClick={onPublish} disabled={publishing || !deliveryReady || !hardwareReady}><Upload size={15}/>{publishing ? "正在发布…" : !hardwareReady ? "等待五金配置完成" : deliveryReady ? "发布组件版本" : "等待制作验收通过"}</button>
    <button className="ghost full" onClick={onExport}><Download size={15}/>导出组件草稿</button>
  </>;
}
