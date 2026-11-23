import type { ComponentMember, Mate } from "./component.ts";
import type { Bounds3 } from "./interfaceGeometry.ts";
import { alignInterfaceFaces, interfaceFrame } from "./faceAlignment.ts";
import { Euler, Quaternion, Vector3 } from "three";

export function solveConnectedFaces(members: ComponentMember[], mates: Mate[], bounds: Map<string, Bounds3>): ComponentMember[] {
  const result = structuredClone(members);
  const visited = new Set<string>();
  for (const root of result) {
    if (visited.has(root.id)) continue;
    visited.add(root.id);
    const queue = [root];
    while (queue.length) {
      const source = queue.shift()!;
      for (const mate of mates) {
        const forward = mate.from.memberId === source.id;
        if (!forward && mate.to.memberId !== source.id) continue;
        if (!["贴合", "同轴"].includes(mate.relation) || mate.offset !== 0) throw new Error("当前自动重算仅支持零偏移贴合或同轴连接。");
        const from = forward ? mate.from : mate.to, to = forward ? mate.to : mate.from;
        const target = result.find(member => member.id === to.memberId);
        const a = source.interfaces.find(item => item.id === from.interfaceId);
        const b = target?.interfaces.find(item => item.id === to.interfaceId);
        const ab = bounds.get(source.id), bb = target && bounds.get(target.id);
        if (!target || !a || !b || !ab || !bb) throw new Error("参数变化后连接接口不存在，请检查接口及连接。");
        if (!visited.has(target.id)) {
          Object.assign(target, alignInterfaceFaces(source, ab, a, target, bb, b));
          visited.add(target.id); queue.push(target);
        }
      }
    }
  }
  for (const mate of mates) {
    const a = result.find(m => m.id === mate.from.memberId)!, b = result.find(m => m.id === mate.to.memberId)!;
    const af = interfaceFrame(bounds.get(a.id)!, a.interfaces.find(i => i.id === mate.from.interfaceId)!);
    const bf = interfaceFrame(bounds.get(b.id)!, b.interfaces.find(i => i.id === mate.to.interfaceId)!);
    const aq = new Quaternion().setFromEuler(new Euler(...a.rotation)), bq = new Quaternion().setFromEuler(new Euler(...b.rotation));
    const distance = af.center.applyQuaternion(aq).add(new Vector3(...a.position)).distanceTo(bf.center.applyQuaternion(bq).add(new Vector3(...b.position)));
    if (distance > 1e-5 || af.normal.applyQuaternion(aq).dot(bf.normal.applyQuaternion(bq)) > -0.999999) throw new Error("参数变化后连接约束冲突，无法同时贴合所有连接面。");
  }
  return result;
}
