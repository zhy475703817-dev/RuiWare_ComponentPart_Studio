from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import PartInterface


RULE_VERSION = "interface-compatibility-v1"
_RELATIONS = {"plane-contact": {"贴合", "重合"}, "coaxial-fit": {"同轴"}, "pin-hole": {"同轴"}}
_ROLES = {"plane-contact": ("contact-face", "contact-face"), "coaxial-fit": ("shaft", "bore"), "pin-hole": ("pin", "hole")}
_GEOMETRY = {"plane-contact": "plane", "coaxial-fit": "axis", "pin-hole": "axis"}
_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


@dataclass(frozen=True)
class Check:
    code: str
    status: Literal["pass", "fail", "unknown"]
    message: str


@dataclass(frozen=True)
class Assessment:
    status: Literal["compatible", "incompatible", "needs_review"]
    ruleVersion: str
    checks: tuple[Check, ...]

    @property
    def compatible(self) -> bool:
        return self.status == "compatible"


def _range(dimension, parameter_values=None, parameter_definitions=None):
    if dimension is None or dimension.unit not in _SCALE:
        return None
    nominal = dimension.nominal
    if dimension.parameterRef:
        definition = next((item for item in parameter_definitions or [] if item.id == dimension.parameterRef), None)
        value = (parameter_values or {}).get(dimension.parameterRef)
        if definition is None or definition.unit != dimension.unit or definition.valueType not in ("number", "integer") or not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        nominal = value
    low = (nominal + dimension.lowerTolerance) * _SCALE[dimension.unit]
    high = (nominal + dimension.upperTolerance) * _SCALE[dimension.unit]
    return (low, high) if nominal > 0 and low > 0 and high >= low else None


def assess_interface_compatibility(a: PartInterface | None, b: PartInterface | None, relation: str | None = None,
                                   context_a=None, context_b=None) -> Assessment:
    checks: list[Check] = []

    def add(code, status, message):
        checks.append(Check(code, status, message))

    if a is None or b is None:
        add("MISSING_INTERFACE", "fail", "接口数据缺失")
    elif a.mating is None or b.mating is None:
        add("MISSING_CONTRACT", "unknown", "至少一端没有版本化配合契约")
    else:
        left, right = a.mating, b.mating
        if left.version != 1 or right.version != 1:
            add("RULE_VERSION_UNSUPPORTED", "unknown", "接口契约版本不受当前规则支持")
        else:
            add("CONTRACT_VERSION", "pass", "接口契约版本受支持")
        if left.family != right.family:
            add("FAMILY_MISMATCH", "fail", "两端配合族不同")
        elif left.family not in _RELATIONS:
            add("UNKNOWN_FAMILY", "unknown", f"当前没有 {left.family} 配合族适配器")
        else:
            add("FAMILY_MATCH", "pass", f"配合族匹配：{left.family}")
        roles = _ROLES.get(left.family)
        if roles:
            normalize_role = lambda role: "contact-face" if left.family == "plane-contact" and role == "surface" else role
            pair = (normalize_role(left.role), normalize_role(right.role))
            matched = all(role in ("pin", "hole") for role in pair) if left.family == "pin-hole" else pair == roles or pair == roles[::-1]
            expected_roles = "surface ↔ surface（兼容 contact-face）" if left.family == "plane-contact" else "pin ↔ pin、hole ↔ hole 或 pin ↔ hole" if left.family == "pin-hole" else f"{roles[0]} ↔ {roles[1]}"
            add("ROLE_COMPLEMENTARY", "pass" if matched else "fail", "配合角色符合规则" if matched else
                f"配合角色不符合 {left.family} 规则：“{a.name}”为 {left.role}，“{b.name}”为 {right.role}；要求 {expected_roles}。请在模板端核对 mating.role 并发布更新版本。")
        elif left.role and right.role:
            add("ROLE_UNSUPPORTED", "unknown", "当前没有该配合族的角色校验规则")
        kind = _GEOMETRY.get(left.family)
        if kind:
            matched = a.type == kind and b.type == kind
            add("GEOMETRY_KIND", "pass" if matched else "fail", "几何载体符合配合族要求" if matched else f"该配合族要求两端几何载体为 {kind}")
            valid_frame = lambda frame: isinstance(frame, dict) and frame.get("axis") in ("x", "y", "z", "-x", "-y", "-z")
            if not valid_frame(a.referenceFrame) or not valid_frame(b.referenceFrame):
                add("FRAME_INVALID", "unknown", "至少一端缺少有效的局部配合参考系")
            else:
                add("FRAME_DECLARED", "pass", "两端局部配合参考系已声明；空间误差仍需装配求解验证")
        family_relations = _RELATIONS.get(left.family)
        if family_relations:
            shared = set(left.allowedRelations).intersection(right.allowedRelations, family_relations)
            matched = relation in shared if relation else bool(shared)
            add("RELATION_ALLOWED" if relation else "RELATION_AVAILABLE", "pass" if matched else "fail",
                f"两端均允许 {relation}" if relation and matched else f"两端不共同支持 {relation}" if relation else f"存在共同配合关系：{'、'.join(sorted(shared))}" if matched else "两端没有共同支持的配合关系")
        if left.standardId and right.standardId and left.standardId != right.standardId:
            add("STANDARD_MISMATCH", "fail", "两端标准 ID 不匹配")
        elif bool(left.standardId) != bool(right.standardId):
            add("STANDARD_UNDECLARED", "unknown", "只有一端声明了标准 ID")
        elif left.standardId:
            add("STANDARD_MATCH", "pass", "标准 ID 匹配")
        if left.family in ("coaxial-fit", "pin-hole"):
            shaft_is_left = left.role in ("shaft", "pin")
            shaft_contract, bore_contract = (left, right) if shaft_is_left else (right, left)
            shaft_context, bore_context = (context_a, context_b) if shaft_is_left else (context_b, context_a)
            shaft_range = _range(shaft_contract.dimensions.get("diameter"), getattr(shaft_context, "parameterValues", None), getattr(shaft_context, "parameterDefinitions", None))
            bore_range = _range(bore_contract.dimensions.get("diameter"), getattr(bore_context, "parameterValues", None), getattr(bore_context, "parameterDefinitions", None))
            if shaft_range is None or bore_range is None:
                add("DIMENSION_UNRESOLVED", "unknown", "销/轴与孔的直径、公差或参数值未完整声明")
            elif not left.connectionMethod or left.connectionMethod != right.connectionMethod:
                add("CONNECTION_METHOD_UNKNOWN", "fail" if left.connectionMethod and right.connectionMethod else "unknown", "两端需声明相同且受支持的配合机制")
            elif left.family == "pin-hole" and left.role == right.role and left.role in ("pin", "hole"):
                passed = max(shaft_range[0], bore_range[0]) <= min(shaft_range[1], bore_range[1]) + 1e-9
                add("DIAMETER_FIT", "pass" if passed else "fail", f"同角色接口直径公差区间{'重叠，可同轴连接' if passed else '不重叠，无法匹配'}")
            else:
                min_clearance, max_clearance = bore_range[0] - shaft_range[1], bore_range[1] - shaft_range[0]
                passed = min_clearance >= -1e-9 if left.connectionMethod == "clearance-fit" else max_clearance <= 1e-9 if left.connectionMethod == "interference-fit" else min_clearance <= 1e-9 and max_clearance >= -1e-9 if left.connectionMethod == "transition-fit" else None
                if passed is None:
                    add("CONNECTION_METHOD_UNKNOWN", "unknown", f"不支持配合机制：{left.connectionMethod}")
                else:
                    add("DIAMETER_FIT", "pass" if passed else "fail", f"直径极限尺寸{'符合' if passed else '不符合'}{left.connectionMethod}要求（间隙 {min_clearance:.3f} 至 {max_clearance:.3f} mm）")
        if left.connectionMethod and right.connectionMethod and left.connectionMethod != right.connectionMethod:
            add("CONNECTION_METHOD_MISMATCH", "fail", "两端连接机制不匹配")

    status = "incompatible" if any(item.status == "fail" for item in checks) else "needs_review" if any(item.status == "unknown" for item in checks) else "compatible"
    return Assessment(status, RULE_VERSION, tuple(checks))
