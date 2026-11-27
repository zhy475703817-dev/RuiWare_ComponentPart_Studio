import { Box3, Euler, Matrix4, Quaternion, Vector3, Vector4 } from "three";
import type { Camera } from "three";
import type { ComponentMember, Vector3 as Position } from "../domain/component.ts";

export type ScreenPoint = { x: number; y: number };
export type ScreenRect = { left: number; top: number; right: number; bottom: number };
export type SelectionRectangle = ScreenRect & { crossing: boolean };
export type SelectionMode = "replace" | "add" | "remove";
export type MemberTransform = { id: string; position: Position; rotation: Position };

/** A right-to-left drag crosses objects; a left-to-right drag contains them. */
export function selectionRectangle(start: ScreenPoint, end: ScreenPoint): SelectionRectangle {
  return {
    left: Math.min(start.x, end.x), top: Math.min(start.y, end.y),
    right: Math.max(start.x, end.x), bottom: Math.max(start.y, end.y),
    crossing: end.x < start.x,
  };
}

function finiteRect(rect: ScreenRect): boolean {
  return [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite)
    && rect.left <= rect.right && rect.top <= rect.bottom;
}

export function boundsInSelection(bounds: ScreenRect | null, selection: SelectionRectangle): boolean {
  if (!bounds || !finiteRect(bounds) || !finiteRect(selection)) return false;
  return selection.crossing
    ? bounds.right >= selection.left && bounds.left <= selection.right && bounds.bottom >= selection.top && bounds.top <= selection.bottom
    : bounds.left >= selection.left && bounds.right <= selection.right && bounds.top >= selection.top && bounds.bottom <= selection.bottom;
}

/** Keep selection order stable while adding or removing members. */
export function updateSelection(current: readonly string[], candidates: readonly string[], mode: SelectionMode = "replace"): string[] {
  const incoming = new Set(candidates);
  if (mode === "remove") return current.filter((id, index) => !incoming.has(id) && current.indexOf(id) === index);
  return [...new Set(mode === "add" ? [...current, ...candidates] : candidates)];
}

const BOX_EDGES = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];

/** Clip an edge to the camera depth range before dividing by w. */
function depthClippedEdge(a: Vector4, b: Vector4): [Vector4, Vector4] | null {
  let first = 0, last = 1;
  for (const [distanceA, distanceB] of [[a.z + a.w, b.z + b.w], [a.w - a.z, b.w - b.z]]) {
    if (distanceA < 0 && distanceB < 0) return null;
    if ((distanceA < 0) !== (distanceB < 0)) {
      const crossing = distanceA / (distanceA - distanceB);
      if (distanceA < 0) first = Math.max(first, crossing);
      else last = Math.min(last, crossing);
    }
  }
  if (first > last) return null;
  return [a.clone().lerp(b, first), a.clone().lerp(b, last)];
}

/** Project world bounds without selecting models behind the camera or far plane. */
export function projectBoxBounds(box: Box3, camera: Camera, viewport: { width: number; height: number }): ScreenRect | null {
  if (box.isEmpty() || ![...box.min.toArray(), ...box.max.toArray(), viewport.width, viewport.height].every(Number.isFinite)
      || viewport.width <= 0 || viewport.height <= 0) return null;
  const projection = new Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  const corners = Array.from({ length: 8 }, (_, index) => new Vector4(
    index & 1 ? box.max.x : box.min.x,
    index & 2 ? box.max.y : box.min.y,
    index & 4 ? box.max.z : box.min.z,
    1,
  ).applyMatrix4(projection));
  const points = BOX_EDGES.flatMap(([a, b]) => depthClippedEdge(corners[a], corners[b]) ?? []);
  const projected = points.filter(point => point.w > 0).map(point => ({
    x: (point.x / point.w + 1) * viewport.width / 2,
    y: (1 - point.y / point.w) * viewport.height / 2,
  })).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!projected.length) return null;
  return {
    left: Math.min(...projected.map(point => point.x)), top: Math.min(...projected.map(point => point.y)),
    right: Math.max(...projected.map(point => point.x)), bottom: Math.max(...projected.map(point => point.y)),
  };
}

export function selectionPivot(members: readonly Pick<ComponentMember, "position">[]): Position {
  const bounds = new Box3();
  for (const member of members) if (member.position.every(Number.isFinite)) bounds.expandByPoint(new Vector3(...member.position));
  return bounds.isEmpty() ? [0, 0, 0] : bounds.getCenter(new Vector3()).toArray() as Position;
}

/** World rotation that takes the active member from its current pose to an exact input pose. */
export function rotationDelta(from: Position, to: Position): Position {
  const delta = new Quaternion().setFromEuler(new Euler(...to))
    .multiply(new Quaternion().setFromEuler(new Euler(...from)).invert());
  const angles = new Euler().setFromQuaternion(delta, "XYZ");
  return [angles.x, angles.y, angles.z];
}

/** Apply one world-space rigid transform to all selected members around a common pivot. */
export function transformSelection(members: readonly Pick<ComponentMember, "id" | "position" | "rotation">[], pivot: Position, position: Position, rotation: Position): MemberTransform[] {
  const origin = new Vector3(...pivot);
  const destination = new Vector3(...position);
  const delta = new Quaternion().setFromEuler(new Euler(...rotation));
  return members.map(member => {
    const transformedPosition = new Vector3(...member.position).sub(origin).applyQuaternion(delta).add(destination);
    const transformedRotation = new Euler().setFromQuaternion(delta.clone().multiply(new Quaternion().setFromEuler(new Euler(...member.rotation))), "XYZ");
    return { id: member.id, position: transformedPosition.toArray() as Position, rotation: [transformedRotation.x, transformedRotation.y, transformedRotation.z] };
  });
}
