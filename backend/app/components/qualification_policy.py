"""Server-owned authoring contracts and revision-bound completion evidence."""
import hashlib
import itertools
import json
import math

from pydantic import Field, model_validator
from .models import ComponentModel, Revision, Vector3
from .brace_policy import active_brace_rules

QUALIFICATION_VERSION = "authoring-qualification-6"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class QualificationConfig(ComponentModel):
    axes: list[int] | None = Field(default=None, max_length=3)
    minimumDimensions: Vector3 | None = None
    maximumDimensions: Vector3 | None = None
    targets: list[Vector3] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_contract(self):
        if self.axes is not None and (len(set(self.axes)) != len(self.axes) or any(type(a) is not int or a not in (0, 1, 2) for a in self.axes)):
            raise ValueError("调整方向必须为不重复的 X/Y/Z")
        if (self.minimumDimensions is None) != (self.maximumDimensions is None):
            raise ValueError("最小和最大尺寸必须同时填写")
        if self.minimumDimensions is not None:
            if any(lo <= 0 or hi < lo for lo, hi in zip(self.minimumDimensions, self.maximumDimensions)):
                raise ValueError("尺寸范围必须为正数且最小值不大于最大值")
        if any(v <= 0 for target in self.targets for v in target):
            raise ValueError("测试尺寸必须为正数")
        if self.minimumDimensions is not None and any(
            not lo <= v <= hi for target in self.targets
            for v, lo, hi in zip(target, self.minimumDimensions, self.maximumDimensions)
        ):
            raise ValueError("额外测试尺寸必须位于声明的使用范围内")
        return self


class QualificationRequest(ComponentModel):
    expectedRevision: Revision
    retry: bool = False


class QualificationConfigRequest(ComponentModel):
    expectedRevision: Revision
    config: QualificationConfig


def design_key(document, design=None):
    selected = design if design is not None else document.get("geometryDesign") or {}
    return digest({"locks": selected.get("locks", []), "relations": selected.get("relations", []),
                   "braceModes": [(r['id'], r.get('enabled', True)) for r in document.get('braceGenerationRules', [])]})


def brace_height_grids(document):
    """Reject-remainder rules constrain the Z input used by ResizeSolver."""
    values = {p["id"]: p.get("value") for p in (document or {}).get("parameters", [])}
    grids = []
    for rule in active_brace_rules(document or {}):
        if rule.get("remainderPolicy", "reject") != "reject":
            continue
        offset = values[rule["bottomOffsetParameterId"]] + values[rule["topClearanceParameterId"]]
        step = values[rule["pitchParameterId"]] * rule.get("columnInterfaceIndexStep", 1)
        if step <= 0:
            raise ValueError("撑杆规则孔距必须为正数")
        grids.append((offset, step))
    return grids


def brace_height_supported(height, grids):
    return all(height >= offset + step - 1e-6
               and abs((height-offset) / step - round((height-offset) / step)) * step <= 1e-6
               for offset, step in grids)


def continuation_dimensions(source, target, document):
    """Choose one legal intermediate, preserving the caller's final target."""
    midpoint = [(a + b) / 2 for a, b in zip(source, target)]
    grids = brace_height_grids(document)
    if grids:
        if not brace_height_supported(source[2], grids) or not brace_height_supported(target[2], grids):
            return None
        if abs(source[2] - target[2]) <= 1e-6:
            midpoint[2] = source[2]
        else:
            lower, upper = sorted((source[2], target[2]))
            # Enumerate the coarsest grid nearest the midpoint first. All
            # rules must accept it; neither endpoint is a continuation step.
            offset, step = max(grids, key=lambda grid: grid[1])
            below = math.floor((midpoint[2] - offset) / step)
            height = None
            for distance in range(10001):
                pair = (offset + (below - distance) * step,
                        offset + (below + 1 + distance) * step)
                height = next((value for value in sorted(pair, key=lambda value: abs(value - midpoint[2]))
                               if lower + 1e-6 < value < upper - 1e-6
                               and brace_height_supported(value, grids)), None)
                if height is not None or pair[0] <= lower and pair[1] >= upper:
                    break
            # Adjacent legal heights have no intermediate Z. Other axes can
            # still advance while retaining the original legal height.
            midpoint[2] = source[2] if height is None else height
    if all(abs(a - b) <= 1e-6 for a, b in zip(midpoint, source)) or all(
            abs(a - b) <= 1e-6 for a, b in zip(midpoint, target)):
        return None
    return midpoint


def scenario_plan(dimensions, axes, config, document=None):
    """Every requested direction, both signs, and every corner of the envelope."""
    lower = config["minimumDimensions"] or [v * (.9 if i in axes else 1) for i, v in enumerate(dimensions)]
    upper = config["maximumDimensions"] or [v * (1.1 if i in axes else 1) for i, v in enumerate(dimensions)]
    grids = brace_height_grids(document)
    if grids and 2 in axes and config["minimumDimensions"] is None:
        offset, step = grids[0]
        # Only inferred samples may be snapped. Explicit range endpoints and
        # requested targets must still fail if the design cannot support them.
        lower, upper = list(lower), list(upper)
        for limit, direction in ((lower, -1), (upper, 1)):
            slot = (math.floor if direction < 0 else math.ceil)((limit[2]-offset) / step)
            candidate = None
            for _ in range(10001):
                height = offset + slot * step
                if height <= 0:
                    break
                if brace_height_supported(height, grids):
                    candidate = height
                    break
                slot += direction
            if candidate is None:
                raise ValueError("撑杆孔距下未找到可用于双向试调的合法高度，请明确支持的使用尺寸")
            limit[2] = candidate
    if any(lo > v + .1 or hi < v - .1 for lo, v, hi in zip(lower, dimensions, upper)):
        raise ValueError("当前实际尺寸不在声明的使用范围内，请先修正使用范围或模型")
    cases, seen = [], {tuple(round(v, 6) for v in dimensions)}
    def add(label, target):
        key = tuple(round(v, 6) for v in target)
        if key not in seen:
            seen.add(key); cases.append({"label": label, "dimensions": list(target), "status": "pending"})
    for axis in axes:
        for name, limit in (("缩小", lower), ("放大", upper)):
            target = list(dimensions); target[axis] = limit[axis]
            add("XYZ"[axis] + name, target)
    if len(axes) > 1:
        for choices in itertools.product((False, True), repeat=len(axes)):
            target = list(dimensions)
            for axis, high in zip(axes, choices): target[axis] = (upper if high else lower)[axis]
            add("组合调整 " + "/".join("XYZ"[a]+("增" if high else "减") for a, high in zip(axes, choices)), target)
    for target in config["targets"]: add("指定使用尺寸", target)
    return cases, lower, upper
