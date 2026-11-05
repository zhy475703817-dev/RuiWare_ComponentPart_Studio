import { Euler, Matrix4, Vector3 } from "three";
import type { ComponentDocument, ComponentPort, ConnectorFrame } from "./component.ts";

export const PORT_RELATIONS = ["固定", "贴合", "同轴", "重合"] as const;

export function resolvePort(document: ComponentDocument, port: ComponentPort) {
  const member = document.assembly.members.find(item => item.id === port.source.memberId);
  const source = member?.interfaces.find(item => item.id === port.source.interfaceId);
  const issues: { code: string; message: string }[] = [];
  if (!member || !source) issues.push({ code: "SOURCE_MISSING", message: "来源成员或接口已失效，请重新选择来源" });
  else if (!source.frame) issues.push({ code: "FRAME_REQUIRED", message: "来源接口未校准，需补充来源接口坐标后复核" });
  if (document.ports.filter(item => item.source.memberId === port.source.memberId && item.source.interfaceId === port.source.interfaceId).length > 1) issues.push({ code: "DUPLICATE_SOURCE", message: "同一来源接口被重复暴露" });
  if (!port.id.trim() || document.ports.filter(item => item.id === port.id).length > 1) issues.push({ code: "INVALID_ID", message: "接口 ID 为空或重复" });
  if (!port.name.trim()) issues.push({ code: "NAME_REQUIRED", message: "请填写对外接口名称" });
  if (!port.compatibleTags?.some(tag => tag.trim())) issues.push({ code: "COMPATIBILITY_REQUIRED", message: "尚未定义可连接对象的标签" });
  if (!port.allowedRelations?.length) issues.push({ code: "RELATIONS_REQUIRED", message: "尚未选择允许的连接方式" });
  let frame: ConnectorFrame | null = null;
  if (member && source?.frame) {
    const matrix = (origin: number[], rotation: number[]) => new Matrix4().makeRotationFromEuler(new Euler(rotation[0], rotation[1], rotation[2], "XYZ")).setPosition(...origin as [number, number, number]);
    const world = matrix(member.position, member.rotation).multiply(matrix(source.frame.origin, source.frame.rotation));
    // Use the solver's singularity threshold so near-90° frames keep the same precision.
    const m = world.elements;
    const y = Math.asin(Math.max(-1, Math.min(1, m[8])));
    const rotation: ConnectorFrame["rotation"] = Math.abs(Math.cos(y)) > 1e-8
      ? [Math.atan2(-m[9], m[10]), y, Math.atan2(-m[4], m[0])]
      : [Math.atan2(m[6], m[5]), y, 0];
    frame = { origin: new Vector3().setFromMatrixPosition(world).toArray(), rotation };
  }
  return { portId: port.id, status: issues.length ? "needs-review" as const : "ready" as const,
    type: source?.type ?? null, frame, sourceVersion: member?.template ?? null, issues };
}
