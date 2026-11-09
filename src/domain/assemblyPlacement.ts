import type { Vector3 as Position } from "./component.ts";

type Bounds = { min: { x: number; y: number; z: number }; max: { x: number; y: number; z: number } };

/** Keep engineering geometry intact; only the new instance gets a placement. */
export function placeNextMember(bounds: Bounds, occupied: Bounds[]): Position {
  const { min, max } = bounds;
  const gap = Math.max(20, (max.x - min.x) * .1);
  const right = occupied.filter(box => box.max.x >= box.min.x).map(box => box.max.x);
  return [right.length ? Math.max(...right) + gap - min.x : -(min.x + max.x) / 2 || 0,
    -(min.y + max.y) / 2 || 0, -min.z || 0];
}
