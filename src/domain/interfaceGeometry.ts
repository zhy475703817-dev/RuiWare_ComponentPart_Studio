import type { ComponentMember, InterfaceSelection, PartInterface, ResolvedPartInterface, Vector3 } from "./component.ts";

export type Bounds3 = { min: Vector3; max: Vector3 };
export type InterfaceFace = "front" | "back" | "left" | "right" | "start" | "end";
export type InterfaceHighlight = {
  face: InterfaceFace;
  corners: [Vector3, Vector3, Vector3, Vector3];
  region: boolean;
};

export function sameInterfaceSelection(a: InterfaceSelection | null, b: InterfaceSelection | null): boolean {
  return a?.memberId === b?.memberId && a?.interfaceId === b?.interfaceId;
}

export function hasInterfaceSelection(members: ComponentMember[], selection: InterfaceSelection): boolean {
  return members.some(member => member.id === selection.memberId && member.interfaces.some(item => item.id === selection.interfaceId));
}

const faceRefs: Record<string, InterfaceFace> = {
  "part.face.front": "front",
  "part.face.back": "back",
  "part.face.left": "left",
  "part.face.right": "right",
  "part.endFace.start": "start",
  "part.endFace.end": "end",
};

export function semanticFaceFromRef(reference: string | undefined): InterfaceFace | null {
  return reference ? faceRefs[reference] || null : null;
}

export function supportsInterfacePreview(item: PartInterface, resolved?: ResolvedPartInterface[]): boolean {
  if (item.declarationMode === "featureDerived") {
    const occurrences = resolved?.filter(entry => entry.id === item.id || entry.sourceInterfaceId === item.id) || [];
    return occurrences.some(entry => hasRegion(entry.region) && entry.geometryRefs.some(reference => semanticFaceFromRef(reference) !== null)) ||
      (hasRegion(item.region) && (item.geometryRefs || []).some(reference => semanticFaceFromRef(reference) !== null));
  }
  return (hasRegion(item.region) || (item.geometryRefs || []).some(reference => semanticFaceFromRef(reference) !== null));
}

function hasRegion(region: PartInterface["region"] | ResolvedPartInterface["region"]): boolean {
  return region?.mode === "rectangle" &&
    typeof region.uStart === "number" && Number.isFinite(region.uStart) &&
    typeof region.vStart === "number" && Number.isFinite(region.vStart) &&
    typeof region.uSpan === "number" && Number.isFinite(region.uSpan) && region.uSpan > 0 &&
    typeof region.vSpan === "number" && Number.isFinite(region.vSpan) && region.vSpan > 0;
}

function positive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

function faceHighlight(bounds: Bounds3, face: InterfaceFace, region?: PartInterface["region"]): InterfaceHighlight {
  const min = bounds.min;
  const max = bounds.max;
  const midpoint = (a: number, b: number) => (a + b) / 2;
  const hasRegion = region?.mode === "rectangle" &&
    typeof region.uStart === "number" && Number.isFinite(region.uStart) &&
    typeof region.vStart === "number" && Number.isFinite(region.vStart) &&
    typeof region.uSpan === "number" && Number.isFinite(region.uSpan) && region.uSpan > 0 &&
    typeof region.vSpan === "number" && Number.isFinite(region.vSpan) && region.vSpan > 0;
  const offset = 0.6;

  switch (face) {
    case "front":
    case "back": {
      const y = face === "front" ? min[1] - offset : max[1] + offset;
      const u = hasRegion ? region.uStart! : midpoint(min[0], max[0]);
      const v = hasRegion ? region.vStart! : midpoint(min[2], max[2]);
      const halfU = (hasRegion ? positive(region.uSpan, max[0] - min[0]) : max[0] - min[0]) / 2;
      const halfV = (hasRegion ? positive(region.vSpan, max[2] - min[2]) : max[2] - min[2]) / 2;
      return {
        face,
        corners: [[u - halfU, y, v - halfV], [u + halfU, y, v - halfV], [u + halfU, y, v + halfV], [u - halfU, y, v + halfV]],
        region: hasRegion,
      };
    }
    case "left":
    case "right": {
      const x = face === "left" ? min[0] - offset : max[0] + offset;
      const u = hasRegion ? region.uStart! : midpoint(min[1], max[1]);
      const v = hasRegion ? region.vStart! : midpoint(min[2], max[2]);
      const halfU = (hasRegion ? positive(region.uSpan, max[1] - min[1]) : max[1] - min[1]) / 2;
      const halfV = (hasRegion ? positive(region.vSpan, max[2] - min[2]) : max[2] - min[2]) / 2;
      return {
        face,
        corners: [[x, u - halfU, v - halfV], [x, u + halfU, v - halfV], [x, u + halfU, v + halfV], [x, u - halfU, v + halfV]],
        region: hasRegion,
      };
    }
    case "start":
    case "end": {
      const z = face === "start" ? min[2] - offset : max[2] + offset;
      const u = hasRegion ? region.uStart! : midpoint(min[0], max[0]);
      const v = hasRegion ? region.vStart! : midpoint(min[1], max[1]);
      const halfU = (hasRegion ? positive(region.uSpan, max[0] - min[0]) : max[0] - min[0]) / 2;
      const halfV = (hasRegion ? positive(region.vSpan, max[1] - min[1]) : max[1] - min[1]) / 2;
      return {
        face,
        corners: [[u - halfU, v - halfV, z], [u + halfU, v - halfV, z], [u + halfU, v + halfV, z], [u - halfU, v + halfV, z]],
        region: hasRegion,
      };
    }
  }
}

/**
 * Maps semantic references onto the STL bounds. This is a best-effort visual
 * aid, not a reconstruction of CAD topology. Resolved feature occurrences are
 * previewed individually; a resolved empty feature set stays empty.
 */
export function interfaceHighlights(bounds: Bounds3, item: PartInterface, resolved?: ResolvedPartInterface[]): InterfaceHighlight[] {
  const matches = resolved?.filter(entry => entry.id === item.id || entry.sourceInterfaceId === item.id) || [];
  if (item.declarationMode === "featureDerived" && !hasRegion(item.region) && (!resolved || matches.length === 0 || matches.every(entry => !hasRegion(entry.region)))) return [];
  const targets = matches.length
    ? matches.map(entry => ({ geometryRefs: entry.geometryRefs, region: entry.region || item.region }))
    : [{ geometryRefs: item.geometryRefs || [], region: item.region }];
  return targets.flatMap(target => target.geometryRefs.flatMap(reference => {
    const face = semanticFaceFromRef(reference);
    if (item.declarationMode === "featureDerived" && !hasRegion(target.region) && !hasRegion(item.region)) return [];
    return face ? [faceHighlight(bounds, face, target.region)] : [];
  }));
}
