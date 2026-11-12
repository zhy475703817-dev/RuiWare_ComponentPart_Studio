import type { ComponentMember, Vector3 } from "../domain/component.ts";
import { memberBounds, memberTemplate } from "./memberModels.ts";
import { interfacesForReconstruction, loadReconstructionInfo } from "./templatePreview.ts";

export async function loadConnectionGeometry(member: ComponentMember, signal: AbortSignal) {
  if (member.template.templateKind === "component") throw new Error("子组件连接面需要明确的几何映射，暂不能自动重算。");
  const [info, box] = await Promise.all([
    loadReconstructionInfo(memberTemplate(member), signal, member.parameterValues),
    memberBounds({ ...member, position: [0, 0, 0], rotation: [0, 0, 0] }),
  ]);
  signal.throwIfAborted();
  const interfaces = interfacesForReconstruction(info).map(item => {
    const existing = member.interfaces.find(old => old.id === item.id);
    return existing?.connectionTypeId === undefined ? item : { ...item, connectionTypeId: existing.connectionTypeId };
  });
  return { member: { ...member, interfaces, resolvedInterfaces: info.resolvedInterfaces },
    bounds: { min: box.min.toArray() as Vector3, max: box.max.toArray() as Vector3 } };
}
