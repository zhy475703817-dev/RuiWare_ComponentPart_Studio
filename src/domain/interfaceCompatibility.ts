import type { ComponentMember, PartInterface } from "./component";

export type CompatibilityResult = {
  compatible: boolean;
  genderMatch: boolean;
  typeMatch: boolean;
  sizeMatch: boolean;
  methodMatch: boolean;
  reason: string;
};

const maleTags = new Set(["male", "m", "公", "公头", "插头", "plug", "pin"]);
const femaleTags = new Set(["female", "f", "母", "母座", "插座", "socket", "hole"]);

function normalizedTags(item: PartInterface): string[] {
  return item.tags.map(tag => tag.trim().toLowerCase()).filter(Boolean);
}

export function interfaceGender(item: PartInterface | undefined): "male" | "female" | "neutral" {
  if (!item) return "neutral";
  if (item.connectionGender) return item.connectionGender;
  const tags = normalizedTags(item);
  if (tags.some(tag => maleTags.has(tag) || tag.includes("gender:male") || tag.includes("公头"))) return "male";
  if (tags.some(tag => femaleTags.has(tag) || tag.includes("gender:female") || tag.includes("母座"))) return "female";
  return "neutral";
}

function sizeSignature(item: PartInterface): { shape: string; values: number[]; tolerance: number } | null {
  const unitScale = (unit: string | undefined): number => {
    switch (unit?.trim().toLowerCase()) {
      case "m": case "meter": case "meters": case "米": return 1000;
      case "cm": case "centimeter": case "centimeters": case "厘米": return 10;
      case "in": case "inch": case "inches": case "英寸": return 25.4;
      case "ft": case "foot": case "feet": case "英尺": return 304.8;
      default: return 1;
    }
  };
  const size = item.interfaceSize;
  const scale = unitScale(size?.unit);
  if (size?.diameter && size.diameter > 0) return { shape: "round", values: [size.diameter * scale], tolerance: (size.tolerance || 0) * scale };
  if (size) {
    const values = [size.width, size.height, size.depth].filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0).map(value => value * scale).sort((a, b) => a - b);
    if (values.length) return { shape: `rect-${values.length}`, values, tolerance: (size.tolerance || 0) * scale };
  }
  const region = item.region;
  const values = [region?.uSpan, region?.vSpan].filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0).sort((a, b) => a - b);
  return values.length ? { shape: `rect-${values.length}`, values, tolerance: 0 } : null;
}

export function interfacesCompatible(a: PartInterface | undefined, b: PartInterface | undefined): CompatibilityResult {
  if (!a || !b) return { compatible: false, genderMatch: false, typeMatch: false, sizeMatch: false, methodMatch: false, reason: "接口数据缺失" };
  const aGender = interfaceGender(a), bGender = interfaceGender(b);
  // Missing gender metadata is unknown, not a neutral connector that conflicts
  // with a declared male/female end. Only explicit like-to-like genders conflict.
  const genderMatch = aGender === "neutral" || bGender === "neutral" || aGender !== bGender;
  const typeMatch = a.type === b.type;
  const aSize = sizeSignature(a), bSize = sizeSignature(b);
  const sizeMatch = Boolean(aSize && bSize && aSize.shape === bSize.shape && aSize.values.length === bSize.values.length && aSize.values.every((value, index) => {
    const tolerance = Math.max(aSize.tolerance, bSize.tolerance, 0.01);
    return Math.abs(value - bSize.values[index]) <= tolerance;
  }));
  const methodA = a.connectionMethod?.trim().toLowerCase(), methodB = b.connectionMethod?.trim().toLowerCase();
  const methodMatch = !methodA || !methodB || methodA === methodB;
  const compatible = genderMatch && typeMatch && sizeMatch && methodMatch;
  const reason = !genderMatch ? "公母属性不匹配" : !typeMatch ? "接口几何类型不匹配" : !sizeMatch ? (!aSize || !bSize ? "接口尺寸缺失" : "接口尺寸不匹配") : !methodMatch ? "连接方式不匹配" : aGender === "neutral" || bGender === "neutral" ? "尺寸匹配；公母属性未完整声明" : "尺寸与接口属性匹配";
  return { compatible, genderMatch, typeMatch, sizeMatch, methodMatch, reason };
}

export function compatibleMemberIds(members: ComponentMember[], selectedMemberId: string | null): Set<string> {
  const result = new Set<string>();
  if (!selectedMemberId) return result;
  const selected = members.find(member => member.id === selectedMemberId);
  if (!selected) return result;
  for (const member of members) {
    if (member.id === selected.id) continue;
    if (selected.interfaces.some(source => member.interfaces.some(target => interfacesCompatible(source, target).compatible))) result.add(member.id);
  }
  return result;
}
