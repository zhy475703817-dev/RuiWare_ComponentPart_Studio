import type { ComponentMember, MatingDimension, PartInterface } from "./component";

export type CompatibilityStatus = "compatible" | "incompatible" | "needs_review";
export type CompatibilityCheck = { code: string; status: "pass" | "fail" | "unknown"; message: string };
export type CompatibilityAssessment = { status: CompatibilityStatus; compatible: boolean; ruleVersion: string; checks: CompatibilityCheck[]; reason: string };

export const INTERFACE_RULE_VERSION = "interface-compatibility-v1";
/** Legacy display helper only. Compatibility is determined by the mating contract. */
export function interfaceGender(item: PartInterface | undefined): "male" | "female" | "neutral" {
  if (item?.connectionGender) return item.connectionGender;
  const tags = item?.tags.map(tag => tag.trim().toLowerCase()) || [];
  if (tags.some(tag => ["male", "m", "公", "公头", "插头", "plug", "pin"].includes(tag))) return "male";
  if (tags.some(tag => ["female", "f", "母", "母座", "插座", "socket", "hole"].includes(tag))) return "female";
  return "neutral";
}
const relationByFamily: Record<string, string[]> = { "plane-contact": ["贴合", "重合"], "coaxial-fit": ["同轴"], "pin-hole": ["同轴"] };
const rolesByFamily: Record<string, [string, string]> = { "plane-contact": ["contact-face", "contact-face"], "coaxial-fit": ["shaft", "bore"], "pin-hole": ["pin", "hole"] };
const requiredType: Record<string, string> = { "plane-contact": "plane", "coaxial-fit": "axis", "pin-hole": "axis" };
const scales: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4 };
type MemberContext = Pick<ComponentMember, "parameterValues" | "parameterDefinitions">;

function check(checks: CompatibilityCheck[], code: string, status: CompatibilityCheck["status"], message: string) { checks.push({ code, status, message }); }

function range(dimension: MatingDimension | undefined, context?: MemberContext) {
  if (!dimension || !scales[dimension.unit]) return null;
  let nominal = dimension.nominal;
  if (dimension.parameterRef) {
    const value = context?.parameterValues[dimension.parameterRef];
    const definition = context?.parameterDefinitions?.find(item => item.id === dimension.parameterRef);
    if (typeof value !== "number" || !Number.isFinite(value) || !definition || definition.unit !== dimension.unit || !["number", "integer"].includes(definition.valueType)) return null;
    nominal = value;
  }
  if (!Number.isFinite(nominal) || nominal <= 0 || !Number.isFinite(dimension.lowerTolerance) || dimension.lowerTolerance > 0 || !Number.isFinite(dimension.upperTolerance) || dimension.upperTolerance < 0) return null;
  const low = (nominal + dimension.lowerTolerance) * scales[dimension.unit];
  const high = (nominal + dimension.upperTolerance) * scales[dimension.unit];
  return low > 0 && high >= low ? { low, high } : null;
}

function checkFit(a: PartInterface, b: PartInterface, contextA: MemberContext | undefined, contextB: MemberContext | undefined, checks: CompatibilityCheck[]) {
  const left = a.mating!, right = b.mating!;
  const leftShaft = left.role === "shaft" || left.role === "pin";
  const shaft = leftShaft ? left : right, bore = leftShaft ? right : left;
  const shaftContext = leftShaft ? contextA : contextB, boreContext = leftShaft ? contextB : contextA;
  const shaftRange = range(shaft.dimensions.diameter, shaftContext), boreRange = range(bore.dimensions.diameter, boreContext);
  if (!shaftRange || !boreRange) { check(checks, "DIMENSION_UNRESOLVED", "unknown", "销/轴与孔的直径、公差或参数值未完整声明"); return; }
  if (!left.connectionMethod || left.connectionMethod !== right.connectionMethod) {
    check(checks, "CONNECTION_METHOD_UNKNOWN", left.connectionMethod && right.connectionMethod ? "fail" : "unknown", "两端需声明相同且受支持的配合机制"); return;
  }
  const minClearance = boreRange.low - shaftRange.high, maxClearance = boreRange.high - shaftRange.low;
  const passed = left.connectionMethod === "clearance-fit" ? minClearance >= -1e-9
    : left.connectionMethod === "interference-fit" ? maxClearance <= 1e-9
      : left.connectionMethod === "transition-fit" ? minClearance <= 1e-9 && maxClearance >= -1e-9 : undefined;
  if (passed === undefined) { check(checks, "CONNECTION_METHOD_UNKNOWN", "unknown", `不支持配合机制：${left.connectionMethod}`); return; }
  check(checks, "DIAMETER_FIT", passed ? "pass" : "fail", `直径极限尺寸${passed ? "符合" : "不符合"}${left.connectionMethod}要求（间隙 ${minClearance.toFixed(3)} 至 ${maxClearance.toFixed(3)} mm）`);
}

export function assessInterfaceCompatibility(a: PartInterface | undefined, b: PartInterface | undefined,
  relation?: "贴合" | "同轴" | "重合", contextA?: MemberContext, contextB?: MemberContext): CompatibilityAssessment {
  const checks: CompatibilityCheck[] = [];
  if (!a || !b) check(checks, "MISSING_INTERFACE", "fail", "接口数据缺失");
  else if (!a.mating || !b.mating) check(checks, "MISSING_CONTRACT", "unknown", "至少一端没有版本化配合契约");
  else {
    const left = a.mating, right = b.mating;
    if (left.version !== 1 || right.version !== 1) check(checks, "RULE_VERSION_UNSUPPORTED", "unknown", "接口契约版本不受当前规则支持");
    else check(checks, "CONTRACT_VERSION", "pass", "接口契约版本受支持");
    if (left.family !== right.family) check(checks, "FAMILY_MISMATCH", "fail", "两端配合族不同");
    else if (!relationByFamily[left.family]) check(checks, "UNKNOWN_FAMILY", "unknown", `当前没有 ${left.family} 配合族适配器`);
    else check(checks, "FAMILY_MATCH", "pass", `配合族匹配：${left.family}`);
    const rolePair = rolesByFamily[left.family];
    if (!rolePair) { if (left.role && right.role) check(checks, "ROLE_UNSUPPORTED", "unknown", "当前没有该配合族的角色校验规则"); }
    else {
      const rolesMatch = rolePair[0] === rolePair[1] ? left.role === rolePair[0] && right.role === rolePair[1]
        : left.role === rolePair[0] && right.role === rolePair[1] || left.role === rolePair[1] && right.role === rolePair[0];
      check(checks, "ROLE_COMPLEMENTARY", rolesMatch ? "pass" : "fail", rolesMatch ? "配合角色符合规则" : "配合角色不符合该配合族规则");
    }
    const kind = requiredType[left.family];
    if (kind) check(checks, "GEOMETRY_KIND", a.type === kind && b.type === kind ? "pass" : "fail", a.type === kind && b.type === kind ? "几何载体符合配合族要求" : `该配合族要求两端几何载体为 ${kind}`);
    if (kind) {
      const frameA = a.referenceFrame, frameB = b.referenceFrame;
      const validFrame = (frame: PartInterface["referenceFrame"]) => Boolean(frame && ["x", "y", "z", "-x", "-y", "-z"].includes(frame.axis));
      if (!validFrame(frameA) || !validFrame(frameB)) check(checks, "FRAME_INVALID", "unknown", "至少一端缺少有效的局部配合参考系");
      else check(checks, "FRAME_DECLARED", "pass", "两端局部配合参考系已声明；空间误差仍需装配求解验证");
    }
    const allowed = relationByFamily[left.family];
    if (allowed) {
      const shared = left.allowedRelations.filter(value => right.allowedRelations.includes(value) && allowed.includes(value));
      check(checks, relation ? "RELATION_ALLOWED" : "RELATION_AVAILABLE", relation ? shared.includes(relation) ? "pass" : "fail" : shared.length ? "pass" : "fail",
        relation ? shared.includes(relation) ? `两端均允许 ${relation}` : `两端不共同支持 ${relation}` : shared.length ? `存在共同配合关系：${shared.join("、")}` : "两端没有共同支持的配合关系");
    }
    if (left.standardId && right.standardId && left.standardId !== right.standardId) check(checks, "STANDARD_MISMATCH", "fail", "两端标准 ID 不匹配");
    else if ((left.standardId && !right.standardId) || (!left.standardId && right.standardId)) check(checks, "STANDARD_UNDECLARED", "unknown", "只有一端声明了标准 ID");
    else if (left.standardId) check(checks, "STANDARD_MATCH", "pass", "标准 ID 匹配");
    if (left.family === "coaxial-fit" || left.family === "pin-hole") checkFit(a, b, contextA, contextB, checks);
    if (left.connectionMethod && right.connectionMethod && left.connectionMethod !== right.connectionMethod) check(checks, "CONNECTION_METHOD_MISMATCH", "fail", "两端连接机制不匹配");
  }
  const status = checks.some(item => item.status === "fail") ? "incompatible" : checks.some(item => item.status === "unknown") ? "needs_review" : "compatible";
  return { status, compatible: status === "compatible", ruleVersion: INTERFACE_RULE_VERSION, checks,
    reason: checks.find(item => item.status !== "pass")?.message || checks.at(-1)?.message || "接口配合契约通过" };
}

export function interfacesCompatible(a: PartInterface | undefined, b: PartInterface | undefined,
  relation?: "贴合" | "同轴" | "重合", contextA?: MemberContext, contextB?: MemberContext) {
  return assessInterfaceCompatibility(a, b, relation, contextA, contextB);
}

export function compatibleMemberIds(members: ComponentMember[], selectedMemberId: string | null): Set<string> {
  const result = new Set<string>(), selected = members.find(member => member.id === selectedMemberId);
  if (!selected) return result;
  for (const member of members) if (member !== selected && selected.interfaces.some(source => member.interfaces.some(target => assessInterfaceCompatibility(source, target, undefined, selected, member).status === "compatible"))) result.add(member.id);
  return result;
}
