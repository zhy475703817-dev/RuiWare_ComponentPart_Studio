import type { PortTrialPreview } from "../domain/component";
import { ComponentViewport } from "../viewport/ComponentViewport";

export function PortTrialViewport({ preview, onClose }: { preview: PortTrialPreview; onClose: () => void }) {
  const members = [
    ...preview.movingMembers.map(member => ({ ...member, id: "trial-source/" + member.id, name: "本侧 · " + member.name, role: "本侧 · " + member.role })),
    ...preview.targetMembers.map(member => ({ ...member, id: "trial-target/" + member.id, name: "目标 · " + member.name, role: "目标 · " + member.role })),
  ];
  return <div className="port-trial-viewport">
    <div className="port-trial-banner" role="status">对外接口只读试配 · 本侧 R{preview.baseRevision} / 目标 R{preview.targetRevision} · 正式装配未修改<button className="ghost" onClick={onClose}>退出试配</button></div>
    <ComponentViewport members={members} mateCount={0} previewTemplate={null}/>
  </div>;
}
