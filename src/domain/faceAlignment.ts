import { Euler, Quaternion, Vector3 } from "three";
import type { ComponentMember, PartInterface, Vector3 as Position } from "./component.ts";
import { interfaceHighlights, type Bounds3 } from "./interfaceGeometry.ts";

export function interfaceFrame(bounds: Bounds3, item: PartInterface) {
  const highlights = interfaceHighlights(bounds, item);
  if (highlights.length !== 1) throw new Error("接口必须对应一个明确连接面，无法自动贴合。");
  const highlight = highlights[0];
  const normals = { front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], start: [0, 0, -1], end: [0, 0, 1] };
  const normal = new Vector3(...normals[highlight.face] as Position);
  const center = highlight.corners.reduce((sum, point) => sum.add(new Vector3(...point)), new Vector3()).multiplyScalar(0.25);
  // Preview markers sit 0.6 mm above the surface; mating uses the actual surface.
  center.addScaledVector(normal, -0.6);
  return { center, normal };
}

export function alignInterfaceFaces(source: ComponentMember, sourceBounds: Bounds3, a: PartInterface,
  target: ComponentMember, targetBounds: Bounds3, b: PartInterface) {
  const from = interfaceFrame(sourceBounds, a), to = interfaceFrame(targetBounds, b);
  const sourceRotation = new Quaternion().setFromEuler(new Euler(...source.rotation));
  const targetRotation = new Quaternion().setFromEuler(new Euler(...target.rotation));
  const desiredNormal = from.normal.clone().applyQuaternion(sourceRotation).negate();
  const correction = new Quaternion().setFromUnitVectors(to.normal.clone().applyQuaternion(targetRotation), desiredNormal);
  targetRotation.premultiply(correction).normalize();
  const position = from.center.clone().applyQuaternion(sourceRotation).add(new Vector3(...source.position))
    .sub(to.center.clone().applyQuaternion(targetRotation));
  const rotation = new Euler().setFromQuaternion(targetRotation);
  return { position: position.toArray() as Position, rotation: [rotation.x, rotation.y, rotation.z] as Position };
}
