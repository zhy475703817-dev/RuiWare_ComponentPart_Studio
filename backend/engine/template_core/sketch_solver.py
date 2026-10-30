from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .models import SemanticSketchEntity, TemplateDraft
from .rules import RuleEvaluationError, evaluate_expression, resolve_parameters


Point = tuple[float, float]
TOLERANCE = 1e-6


def _point(value: Point | None, fallback: Point = (0.0, 0.0)) -> Point:
    return (float(value[0]), float(value[1])) if value is not None else fallback


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _parameter_values(
    draft: TemplateDraft, case: str, overrides: dict[str, float] | None
) -> tuple[dict[str, float], list[dict[str, str]]]:
    explicit = dict(overrides or {})
    case_overrides = dict(explicit)
    for item in draft.parameterDefinitions:
        source_type = item.sourceDefinition.type if item.sourceDefinition else "userInput"
        if source_type in {"formula", "lookup", "constant"} or item.id in explicit:
            continue
        if case == "minimum" and item.minimum is not None:
            case_overrides[item.id] = float(item.minimum)
        elif case == "maximum" and item.maximum is not None:
            case_overrides[item.id] = float(item.maximum)
    resolved, _, evaluation_diagnostics = resolve_parameters(draft.parameterDefinitions, case_overrides)
    if case == "nominal" and explicit and evaluation_diagnostics:
        relaxed = [
            item.model_copy(update={"minimum": None, "maximum": None, "allowedValues": []})
            if item.id in explicit
            else item
            for item in draft.parameterDefinitions
        ]
        preview, _, _ = resolve_parameters(relaxed, explicit)
        resolved.update(preview)
    values = {
        key: float(value)
        for key, value in resolved.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    diagnostics = [
        {"severity": item.severity, "code": item.code, "path": item.path, "message": item.message}
        for item in evaluation_diagnostics
    ]
    return values, diagnostics


@dataclass
class EntitySlice:
    entity: SemanticSketchEntity
    start: int
    stop: int


def _entity_vector(entity: SemanticSketchEntity) -> list[float]:
    if entity.geometryType == "point":
        return list(_point(entity.start))
    if entity.geometryType == "line":
        return [*_point(entity.start), *_point(entity.end, (10.0, 0.0))]
    if entity.geometryType == "circle":
        return [*_point(entity.center), float(entity.radius or 1.0)]
    if entity.geometryType == "arc":
        center = _point(entity.center)
        radius = float(entity.radius or max(_distance(center, _point(entity.start)), 1.0))
        start_angle = entity.startAngle
        end_angle = entity.endAngle
        if start_angle is None:
            start = _point(entity.start, (center[0] + radius, center[1]))
            start_angle = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0]))
        if end_angle is None:
            end = _point(entity.end, (center[0], center[1] + radius))
            end_angle = math.degrees(math.atan2(end[1] - center[1], end[0] - center[0]))
        return [center[0], center[1], radius, math.radians(start_angle), math.radians(end_angle)]
    return []


def _make_state(draft: TemplateDraft) -> tuple[np.ndarray, dict[str, EntitySlice]]:
    vector: list[float] = []
    slices: dict[str, EntitySlice] = {}
    for entity in draft.sketch.entities:
        values = _entity_vector(entity)
        slices[entity.id] = EntitySlice(entity=entity, start=len(vector), stop=len(vector) + len(values))
        vector.extend(values)
    return np.asarray(vector, dtype=float), slices


def _geometry(state: np.ndarray, item: EntitySlice) -> dict[str, Any]:
    values = state[item.start:item.stop]
    kind = item.entity.geometryType
    if kind == "point":
        point = (float(values[0]), float(values[1]))
        return {"start": point, "end": point, "center": point, "radius": 0.0}
    if kind == "line":
        return {
            "start": (float(values[0]), float(values[1])),
            "end": (float(values[2]), float(values[3])),
            "center": ((float(values[0]) + float(values[2])) / 2, (float(values[1]) + float(values[3])) / 2),
            "radius": 0.0,
        }
    if kind == "circle":
        center = (float(values[0]), float(values[1]))
        return {"start": center, "end": center, "center": center, "radius": float(values[2])}
    if kind == "arc":
        center = (float(values[0]), float(values[1]))
        radius, start_angle, end_angle = float(values[2]), float(values[3]), float(values[4])
        return {
            "start": (center[0] + radius * math.cos(start_angle), center[1] + radius * math.sin(start_angle)),
            "end": (center[0] + radius * math.cos(end_angle), center[1] + radius * math.sin(end_angle)),
            "center": center,
            "radius": radius,
            "startAngle": start_angle,
            "endAngle": end_angle,
        }
    return {"start": (0.0, 0.0), "end": (0.0, 0.0), "center": (0.0, 0.0), "radius": 0.0}


def _vector(a: Point, b: Point) -> Point:
    return b[0] - a[0], b[1] - a[1]


def _length(value: Point) -> float:
    return max(math.hypot(value[0], value[1]), 1e-12)


def _unit(value: Point) -> Point:
    length = _length(value)
    return value[0] / length, value[1] / length


def _dimension_value(constraint: Any, parameters: dict[str, float]) -> float | None:
    if constraint.parameterId and constraint.parameterId in parameters:
        return parameters[constraint.parameterId]
    if constraint.value is not None:
        return float(constraint.value)
    if constraint.expression:
        if constraint.expression in parameters:
            return parameters[constraint.expression]
        try:
            value = evaluate_expression(constraint.expression, parameters)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        except RuleEvaluationError:
            return None
    return None


def _constraint_residuals(
    state: np.ndarray,
    draft: TemplateDraft,
    slices: dict[str, EntitySlice],
    parameters: dict[str, float],
    original: np.ndarray,
    constraint_ids: set[str] | None = None,
) -> tuple[list[float], list[str]]:
    residuals: list[float] = []
    owners: list[str] = []
    constraints = [
        constraint
        for constraint in draft.sketch.constraints
        if constraint.enabled
        and constraint.driving
        and (constraint_ids is None or constraint.id in constraint_ids)
    ]
    referenced_ids = {
        reference
        for constraint in constraints
        for reference in constraint.entityRefs
        if reference in slices
    }
    state_geometries = {
        entity_id: _geometry(state, item)
        for entity_id, item in slices.items()
        if entity_id in referenced_ids
    }
    original_geometries = {
        entity_id: _geometry(original, item)
        for entity_id, item in slices.items()
        if entity_id in referenced_ids
        and any(constraint.constraintType == "fixed" and entity_id in constraint.entityRefs for constraint in constraints)
    }

    def add(owner: str, *values: float) -> None:
        residuals.extend(float(value) for value in values)
        owners.extend([owner] * len(values))

    for constraint in constraints:
        refs = [slices[reference] for reference in constraint.entityRefs if reference in slices]
        geometries = [state_geometries[item.entity.id] for item in refs]
        kind = constraint.constraintType
        if kind in {"coincident", "closed"}:
            endpoint_refs = list(getattr(constraint, "endpointRefs", None) or [])
            if (
                kind == "coincident"
                and len(geometries) == 2
                and len(endpoint_refs) >= 2
            ):
                first_handle = endpoint_refs[0] if endpoint_refs[0] in {"start", "end"} else "end"
                second_handle = endpoint_refs[1] if endpoint_refs[1] in {"start", "end"} else "start"
                first_point = geometries[0][first_handle]
                second_point = geometries[1][second_handle]
                add(
                    constraint.id,
                    first_point[0] - second_point[0],
                    first_point[1] - second_point[1],
                )
            else:
                pairs = list(zip(geometries, geometries[1:]))
                if kind == "closed" and len(geometries) > 1:
                    pairs.append((geometries[-1], geometries[0]))
                for first, second in pairs:
                    add(constraint.id, first["end"][0] - second["start"][0], first["end"][1] - second["start"][1])
        elif kind == "horizontal":
            for geometry in geometries:
                add(constraint.id, geometry["end"][1] - geometry["start"][1])
        elif kind == "vertical":
            for geometry in geometries:
                add(constraint.id, geometry["end"][0] - geometry["start"][0])
        elif kind in {"parallel", "perpendicular"} and len(geometries) > 1:
            reference = _unit(_vector(geometries[0]["start"], geometries[0]["end"]))
            for geometry in geometries[1:]:
                current = _unit(_vector(geometry["start"], geometry["end"]))
                add(constraint.id, reference[0] * current[1] - reference[1] * current[0] if kind == "parallel" else reference[0] * current[0] + reference[1] * current[1])
        elif kind == "concentric" and len(geometries) > 1:
            center = geometries[0]["center"]
            for geometry in geometries[1:]:
                add(constraint.id, geometry["center"][0] - center[0], geometry["center"][1] - center[1])
        elif kind == "equal" and len(geometries) > 1:
            reference = _distance(geometries[0]["start"], geometries[0]["end"])
            if refs[0].entity.geometryType in {"circle", "arc"}:
                reference = geometries[0]["radius"]
            for item, geometry in zip(refs[1:], geometries[1:]):
                current = geometry["radius"] if item.entity.geometryType in {"circle", "arc"} else _distance(geometry["start"], geometry["end"])
                add(constraint.id, current - reference)
        elif kind in {"distance", "distanceX", "distanceY"} and geometries:
            target = _dimension_value(constraint, parameters)
            if target is not None:
                delta = _vector(geometries[0]["start"], geometries[0]["end"])
                measured = _length(delta) if kind == "distance" else abs(delta[0] if kind == "distanceX" else delta[1])
                add(constraint.id, measured - target)
        elif kind in {"radius", "diameter"} and geometries:
            target = _dimension_value(constraint, parameters)
            if target is not None:
                add(constraint.id, geometries[0]["radius"] - (target / 2 if kind == "diameter" else target))
        elif kind == "angle" and geometries:
            target = math.radians(_dimension_value(constraint, parameters) or 0.0)
            direction = _vector(geometries[0]["start"], geometries[0]["end"])
            measured = math.atan2(direction[1], direction[0])
            add(constraint.id, math.atan2(math.sin(measured - target), math.cos(measured - target)))
        elif kind == "fixed":
            for item, geometry in zip(refs, geometries):
                initial = original_geometries[item.entity.id]
                # A line is anchored by its midpoint so dimensional constraints may still change its length.
                add(constraint.id, geometry["center"][0] - initial["center"][0], geometry["center"][1] - initial["center"][1])
                if item.entity.geometryType == "arc":
                    add(
                        constraint.id,
                        geometry["radius"] - initial["radius"],
                        geometry["startAngle"] - initial["startAngle"],
                        geometry["endAngle"] - initial["endAngle"],
                    )
                if item.entity.geometryType == "point":
                    continue
        elif kind == "pointOn" and len(geometries) >= 2:
            point, curve = geometries[0]["center"], geometries[1]
            if refs[1].entity.geometryType == "line":
                direction = _unit(_vector(curve["start"], curve["end"]))
                add(constraint.id, (point[0] - curve["start"][0]) * direction[1] - (point[1] - curve["start"][1]) * direction[0])
            elif refs[1].entity.geometryType in {"circle", "arc"}:
                add(constraint.id, _distance(point, curve["center"]) - curve["radius"])
        elif kind == "tangent" and len(geometries) >= 2:
            first, second = geometries[0], geometries[1]
            types = refs[0].entity.geometryType, refs[1].entity.geometryType
            if "line" in types and ("circle" in types or "arc" in types):
                line = first if types[0] == "line" else second
                circle = second if types[0] == "line" else first
                direction = _unit(_vector(line["start"], line["end"]))
                distance = abs((circle["center"][0] - line["start"][0]) * direction[1] - (circle["center"][1] - line["start"][1]) * direction[0])
                add(constraint.id, distance - circle["radius"])
            elif types[0] in {"circle", "arc"} and types[1] in {"circle", "arc"}:
                add(constraint.id, _distance(first["center"], second["center"]) - first["radius"] - second["radius"])
        elif kind == "symmetric" and len(geometries) == 3:
            first, second, axis = geometries
            origin = axis["start"]
            direction = _unit(_vector(axis["start"], axis["end"]))

            def reflect(point: Point) -> Point:
                relative = point[0] - origin[0], point[1] - origin[1]
                projection = relative[0] * direction[0] + relative[1] * direction[1]
                on_axis = origin[0] + projection * direction[0], origin[1] + projection * direction[1]
                return 2 * on_axis[0] - point[0], 2 * on_axis[1] - point[1]

            reflected_start, reflected_end = reflect(first["start"]), reflect(first["end"])
            # Endpoints may be reversed after reflection, so use the lower-error pairing.
            direct = _distance(reflected_start, second["start"]) + _distance(reflected_end, second["end"])
            reverse = _distance(reflected_start, second["end"]) + _distance(reflected_end, second["start"])
            target_start, target_end = (second["start"], second["end"]) if direct <= reverse else (second["end"], second["start"])
            add(
                constraint.id,
                reflected_start[0] - target_start[0],
                reflected_start[1] - target_start[1],
                reflected_end[0] - target_end[0],
                reflected_end[1] - target_end[1],
            )
    return residuals, owners


def _solve_state(
    draft: TemplateDraft, parameters: dict[str, float]
) -> tuple[np.ndarray, int, list[str], float]:
    initial, slices = _make_state(draft)
    if not len(initial):
        return initial, 0, [], 0.0

    def residual_function(
        state: np.ndarray,
        constraint_ids: set[str] | None = None,
    ) -> tuple[np.ndarray, list[str]]:
        residuals, owners = _constraint_residuals(
            state,
            draft,
            slices,
            parameters,
            initial,
            constraint_ids,
        )
        return np.asarray(residuals or [0.0], dtype=float), owners

    entity_constraints: dict[str, set[str]] = {}
    for constraint in draft.sketch.constraints:
        if constraint.enabled and constraint.driving:
            for entity_id in constraint.entityRefs:
                entity_constraints.setdefault(entity_id, set()).add(constraint.id)
    column_entities: list[str | None] = [None] * len(initial)
    for entity_id, item in slices.items():
        column_entities[item.start:item.stop] = [entity_id] * (item.stop - item.start)
    constraint_columns: dict[str, list[int]] = {}
    for constraint in draft.sketch.constraints:
        if not constraint.enabled or not constraint.driving:
            continue
        columns = [
            column
            for entity_id in constraint.entityRefs
            if entity_id in slices
            for column in range(slices[entity_id].start, slices[entity_id].stop)
        ]
        constraint_columns[constraint.id] = columns

    conflicts = [set() for _ in range(len(initial))]
    for columns in constraint_columns.values():
        for column in columns:
            conflicts[column].update(other for other in columns if other != column)
    colors: list[list[int]] = []
    assigned_colors: dict[int, int] = {}
    active_columns = [column for column, related in enumerate(conflicts) if related]
    for column in sorted(active_columns, key=lambda item: len(conflicts[item]), reverse=True):
        unavailable = {assigned_colors[other] for other in conflicts[column] if other in assigned_colors}
        color = next((index for index in range(len(colors)) if index not in unavailable), len(colors))
        if color == len(colors):
            colors.append([])
        colors[color].append(column)
        assigned_colors[column] = color

    def numerical_jacobian(
        state: np.ndarray,
        residual: np.ndarray,
        owners: list[str],
    ) -> np.ndarray:
        jacobian = np.zeros((len(residual), len(state)), dtype=float)
        owner_rows: dict[str, list[int]] = {}
        for row, owner in enumerate(owners):
            owner_rows.setdefault(owner, []).append(row)
        for columns in colors:
            affected = {
                constraint_id
                for column in columns
                for constraint_id in entity_constraints.get(column_entities[column] or "", set())
            }
            shifted = state.copy()
            steps: dict[int, float] = {}
            for column in columns:
                step = max(1e-7, abs(state[column]) * 1e-7)
                steps[column] = step
                shifted[column] += step
            shifted_residual, shifted_owners = residual_function(shifted, affected)
            shifted_rows: dict[str, list[float]] = {}
            for value, owner in zip(shifted_residual, shifted_owners):
                shifted_rows.setdefault(owner, []).append(float(value))
            for owner, values in shifted_rows.items():
                rows = owner_rows.get(owner, [])
                if len(rows) != len(values):
                    raise ValueError(f"Constraint residual shape changed while solving: {owner}")
                changed_columns = [
                    column for column in columns if column in constraint_columns.get(owner, [])
                ]
                if len(changed_columns) != 1:
                    raise ValueError(f"Constraint columns are not independently colored: {owner}")
                column = changed_columns[0]
                jacobian[rows, column] = (np.asarray(values) - residual[rows]) / steps[column]
        return jacobian

    state = initial.copy()
    residual, owners = residual_function(state)
    damping = 1e-6
    for _ in range(250):
        if np.linalg.norm(residual, ord=np.inf) < 1e-9:
            break
        jacobian = numerical_jacobian(state, residual, owners)
        left = jacobian.T @ jacobian + damping * np.eye(len(state))
        right = -(jacobian.T @ residual)
        try:
            delta = np.linalg.solve(left, right)
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(left, right, rcond=None)[0]
        candidate = state + delta
        candidate_residual, candidate_owners = residual_function(candidate)
        if np.linalg.norm(delta) < 1e-10:
            break
        if np.linalg.norm(candidate_residual) < np.linalg.norm(residual):
            state = candidate
            residual, owners = candidate_residual, candidate_owners
            damping = max(1e-12, damping / 3)
            if np.linalg.norm(delta) < 1e-10 or np.linalg.norm(residual, ord=np.inf) < 1e-9:
                break
        else:
            damping = min(1e12, damping * 10)
            if damping >= 1e12:
                break

    jacobian = numerical_jacobian(state, residual, owners)
    if owners:
        jacobian = jacobian[: len(owners), :]
    # Construction geometry is still part of the solved state: constraints
    # such as horizontal, fixed, symmetry, etc. may use it as a reference.
    # It is not, however, design geometry and its own unconstrained movement
    # must not make the profile report under-constrained.  Compute the
    # dimension of the constraint null-space *projected onto non-construction
    # columns*. This preserves constraints coupling a construction reference
    # to a real profile entity while dropping construction-only motion.
    construction_columns = [
        column
        for entity in draft.sketch.entities
        if entity.construction
        for column in range(slices[entity.id].start, slices[entity.id].stop)
    ]

    def matrix_rank(value: np.ndarray) -> int:
        if not value.size:
            return 0
        singular_values = np.linalg.svd(value, compute_uv=False)
        scale = singular_values[0] if len(singular_values) else 0.0
        tolerance = max(value.shape, default=0) * np.finfo(float).eps * scale
        return int(np.sum(singular_values > max(tolerance, 1e-8)))

    rank = matrix_rank(jacobian)
    profile_columns = [column for column in range(len(initial)) if column not in set(construction_columns)]
    if profile_columns and jacobian.size:
        # SVD gives a stable basis for the null-space even when redundant
        # construction constraints are present. The projected basis rank is
        # the number of remaining design degrees of freedom.
        _, singular_values, vh = np.linalg.svd(jacobian, full_matrices=True)
        nullity = len(initial) - rank
        if nullity:
            null_basis = vh[-nullity:, :].T
            degrees_of_freedom = matrix_rank(null_basis[profile_columns, :])
        else:
            degrees_of_freedom = 0
    else:
        degrees_of_freedom = 0
    redundant: list[str] = []
    previous_rank = 0
    for owner in dict.fromkeys(owners):
        rows = [index for index, value in enumerate(owners) if value == owner]
        upto = [index for index, value in enumerate(owners) if value in dict.fromkeys(owners[: rows[-1] + 1])]
        current_rank = int(np.linalg.matrix_rank(jacobian[upto, :], tol=1e-8)) if upto else 0
        if current_rank == previous_rank:
            redundant.append(owner)
        previous_rank = current_rank
    maximum_residual = max((abs(value) for value in residual), default=0.0)
    return state, degrees_of_freedom, redundant, maximum_residual


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    return _orientation(a, b, c) * _orientation(a, b, d) < -1e-8 and _orientation(c, d, a) * _orientation(c, d, b) < -1e-8


def _case(draft: TemplateDraft, case: str, overrides: dict[str, float] | None) -> dict[str, Any]:
    parameters, diagnostics = _parameter_values(draft, case, overrides)
    state, degrees_of_freedom, redundant, maximum_residual = _solve_state(draft, parameters)
    _, slices = _make_state(draft)
    if maximum_residual > 1e-5:
        diagnostics.append({"severity": "error", "code": "SKETCH_CONSTRAINT_CONFLICT", "path": "sketch.constraints", "message": f"约束无法同时满足，最大残差为 {maximum_residual:.6g} mm。"})
    primitives: list[dict[str, Any]] = []
    all_points: list[Point] = []
    for entity in draft.sketch.entities:
        geometry = _geometry(state, slices[entity.id])
        item: dict[str, Any] = {"id": entity.id, "role": entity.role, "type": entity.geometryType, "construction": entity.construction}
        if entity.geometryType in {"point", "line", "arc"}:
            item.update(start={"x": geometry["start"][0], "y": geometry["start"][1]}, end={"x": geometry["end"][0], "y": geometry["end"][1]})
            all_points.extend([geometry["start"], geometry["end"]])
        if entity.geometryType in {"circle", "arc"}:
            radius = geometry["radius"]
            item.update(center={"x": geometry["center"][0], "y": geometry["center"][1]}, radius=radius)
            if entity.geometryType == "arc":
                item.update(
                    startAngle=math.degrees(geometry["startAngle"]),
                    endAngle=math.degrees(geometry["endAngle"]),
                    largeArc=entity.largeArc,
                )
            all_points.extend([(geometry["center"][0] - radius, geometry["center"][1] - radius), (geometry["center"][0] + radius, geometry["center"][1] + radius)])
            if radius <= TOLERANCE:
                diagnostics.append({"severity": "error", "code": "SKETCH_RADIUS_DEGENERATE", "path": f"sketch.entities.{entity.id}", "message": f"图元 {entity.id} 的半径必须大于0。"})
        if entity.geometryType == "line" and _distance(geometry["start"], geometry["end"]) <= TOLERANCE:
            diagnostics.append({"severity": "error", "code": "SKETCH_ZERO_LENGTH", "path": f"sketch.entities.{entity.id}", "message": f"线段 {entity.id} 退化为零长度。"})
        primitives.append(item)

    region_results: list[dict[str, Any]] = []
    use_centerline_path = draft.sketch.profileMode == "centerlineThinWall" and not draft.sketch.regions
    if use_centerline_path:
        paths = [item for item in draft.sketch.entities if not item.construction and item.geometryType in {"line", "arc"}]
        thickness = float(parameters.get("thickness", 0))
        continuous = bool(paths)
        length = 0.0
        previous_end: Point | None = None
        for entity in paths:
            geometry = _geometry(state, slices[entity.id])
            if previous_end is not None and _distance(previous_end, geometry["start"]) > TOLERANCE:
                continuous = False
            previous_end = geometry["end"]
            length += _distance(geometry["start"], geometry["end"])
        unsupported = [item.id for item in draft.sketch.entities if not item.construction and item.geometryType not in {"line", "arc"}]
        if unsupported:
            diagnostics.append({"severity": "error", "code": "THINWALL_CENTERLINE_TYPE_UNSUPPORTED", "path": "sketch.entities", "message": f"当前中心线薄壁算子仅支持直线和圆弧段：{'、'.join(unsupported)}。"})
        if not continuous:
            diagnostics.append({"severity": "error", "code": "THINWALL_CENTERLINE_DISCONNECTED", "path": "sketch.entities", "message": "中心线薄壁路径必须按图元顺序首尾连续。"})
        if thickness <= TOLERANCE:
            diagnostics.append({"severity": "error", "code": "THINWALL_THICKNESS_INVALID", "path": "parameters.thickness", "message": "中心线薄壁算子的厚度必须大于 0。"})
        region_results.append({
            "id": "section.region.thinwall",
            "operation": "add",
            "boundaryRefs": [item.id for item in paths],
            "closed": continuous and not unsupported and thickness > TOLERANCE,
            "area": length * thickness,
        })
    for region in ([] if use_centerline_path else draft.sketch.regions):
        loop = [slices[reference] for reference in region.boundaryRefs if reference in slices and not slices[reference].entity.construction]
        geometries = [_geometry(state, item) for item in loop]
        closed = bool(loop) and region.closed
        points: list[Point] = []
        if len(loop) == 1 and loop[0].entity.geometryType == "circle":
            area = math.pi * geometries[0]["radius"] ** 2
        else:
            for geometry in geometries:
                if points and _distance(points[-1], geometry["start"]) > TOLERANCE:
                    closed = False
                if not points:
                    points.append(geometry["start"])
                points.append(geometry["end"])
            if points and _distance(points[0], points[-1]) > TOLERANCE:
                closed = False
            polygon = points[:-1] if len(points) > 1 and _distance(points[0], points[-1]) <= TOLERANCE else points
            area = abs(sum(polygon[index][0] * polygon[(index + 1) % len(polygon)][1] - polygon[(index + 1) % len(polygon)][0] * polygon[index][1] for index in range(len(polygon))) / 2) if len(polygon) >= 3 else 0.0
            edges = list(zip(polygon, polygon[1:] + polygon[:1])) if len(polygon) >= 3 else []
            for index, (a, b) in enumerate(edges):
                for other_index, (c, d) in enumerate(edges):
                    if other_index <= index + 1 or (index == 0 and other_index == len(edges) - 1):
                        continue
                    if _segments_intersect(a, b, c, d):
                        diagnostics.append({"severity": "error", "code": "SKETCH_SELF_INTERSECTION", "path": f"sketch.regions.{region.id}", "message": f"区域 {region.id} 边界存在自交。"})
                        break
        if not closed:
            diagnostics.append({"severity": "error", "code": "SKETCH_REGION_OPEN", "path": f"sketch.regions.{region.id}", "message": f"区域 {region.id} 未形成连续闭合环。"})
        if area <= TOLERANCE:
            diagnostics.append({"severity": "error", "code": "SKETCH_REGION_DEGENERATE", "path": f"sketch.regions.{region.id}", "message": f"区域 {region.id} 面积为零。"})
        region_results.append({"id": region.id, "operation": region.operation, "boundaryRefs": region.boundaryRefs, "closed": closed, "area": area})

    if not all_points:
        all_points = [(0.0, 0.0)]
    xs, ys = [point[0] for point in all_points], [point[1] for point in all_points]
    topology_signature = "|".join(f"{item['operation']}:{','.join(item['boundaryRefs'])}" for item in region_results)
    return {
        "case": case,
        "values": parameters,
        "primitives": primitives,
        "segments": [item for item in primitives if item["type"] == "line"],
        "regions": region_results,
        "bounds": {"minimumX": min(xs), "maximumX": max(xs), "minimumY": min(ys), "maximumY": max(ys)},
        "topologySignature": topology_signature,
        "degreesOfFreedom": degrees_of_freedom,
        "redundantConstraints": redundant,
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "diagnostics": diagnostics,
    }


def solve_semantic_sketch(draft: TemplateDraft, overrides: dict[str, float] | None = None) -> dict[str, Any]:
    topology_diagnostics: list[dict[str, str]] = []
    entity_ids = {item.id for item in draft.sketch.entities}
    constraint_contracts: dict[str, tuple[int, int | None, set[str] | None]] = {
        "coincident": (2, None, None), "closed": (3, None, {"line", "arc"}),
        "horizontal": (1, None, {"line"}), "vertical": (1, None, {"line"}),
        "parallel": (2, None, {"line"}), "perpendicular": (2, None, {"line"}),
        "tangent": (2, 2, {"line", "arc", "circle"}), "concentric": (2, None, {"arc", "circle"}),
        "symmetric": (3, 3, {"line", "arc", "circle", "point"}), "equal": (2, None, {"line", "arc", "circle"}),
        "distance": (1, 1, {"line"}), "distanceX": (1, 1, {"line"}), "distanceY": (1, 1, {"line"}),
        "radius": (1, 1, {"arc", "circle"}), "diameter": (1, 1, {"arc", "circle"}), "angle": (1, 1, {"line"}),
        "fixed": (1, None, None), "pointOn": (2, 2, None),
    }
    for constraint in draft.sketch.constraints:
        unknown = set(constraint.entityRefs) - entity_ids
        if unknown:
            topology_diagnostics.append({"severity": "error", "code": "SKETCH_CONSTRAINT_REFERENCE_UNKNOWN", "path": f"sketch.constraints.{constraint.id}", "message": f"约束 {constraint.id} 引用未知图元：{'、'.join(sorted(unknown))}。"})
            continue
        minimum, maximum, allowed = constraint_contracts[constraint.constraintType]
        count = len(constraint.entityRefs)
        if count < minimum or (maximum is not None and count > maximum):
            expected = str(minimum) if maximum == minimum else f"至少 {minimum}"
            topology_diagnostics.append({"severity": "error", "code": "SKETCH_CONSTRAINT_SELECTION_INVALID", "path": f"sketch.constraints.{constraint.id}", "message": f"约束 {constraint.id} 需要选择{expected}个图元，当前为 {count} 个。"})
        if allowed:
            invalid_types = [item.geometryType for item in draft.sketch.entities if item.id in constraint.entityRefs and item.geometryType not in allowed]
            if invalid_types:
                topology_diagnostics.append({"severity": "error", "code": "SKETCH_CONSTRAINT_ENTITY_TYPE_INVALID", "path": f"sketch.constraints.{constraint.id}", "message": f"约束 {constraint.id} 不支持图元类型：{'、'.join(sorted(set(invalid_types)))}。"})
        if constraint.constraintType == "symmetric" and count == 3:
            axis = next((item for item in draft.sketch.entities if item.id == constraint.entityRefs[2]), None)
            if axis is None or axis.geometryType != "line" or not axis.construction:
                topology_diagnostics.append({"severity": "error", "code": "SKETCH_SYMMETRY_AXIS_INVALID", "path": f"sketch.constraints.{constraint.id}", "message": "对称约束的第三个图元必须是构造直线。"})
    for region in ([] if draft.sketch.profileMode == "centerlineThinWall" else draft.sketch.regions):
        unknown = set(region.boundaryRefs) - entity_ids
        if unknown:
            topology_diagnostics.append({"severity": "error", "code": "SKETCH_REGION_REFERENCE_UNKNOWN", "path": f"sketch.regions.{region.id}", "message": f"区域 {region.id} 引用未知图元：{'、'.join(sorted(unknown))}。"})
    cases = [_case(draft, name, overrides if name == "nominal" else None) for name in ("minimum", "nominal", "maximum")]
    signatures = {item["topologySignature"] for item in cases}
    if len(signatures) > 1:
        topology_diagnostics.append({"severity": "error", "code": "SKETCH_TOPOLOGY_UNSTABLE", "path": "sketch.regions", "message": "最小、标称和最大工况的区域拓扑不一致。"})
    nominal = next(item for item in cases if item["case"] == "nominal")
    degrees_of_freedom = nominal["degreesOfFreedom"]
    under = [item.id for item in draft.sketch.entities if not item.construction] if degrees_of_freedom else []
    diagnostics = [*topology_diagnostics, *(item for case in cases for item in case["diagnostics"])]
    if degrees_of_freedom:
        diagnostics.insert(0, {"severity": "warning", "code": "SKETCH_UNDER_CONSTRAINED", "path": "sketch.constraints", "message": f"草图剩余 {degrees_of_freedom} 个自由度。"})
    return {
        "solver": "parametric-sketch-3.0",
        "profileKind": "generic2d",
        "degreesOfFreedom": degrees_of_freedom,
        "fullyConstrained": degrees_of_freedom == 0,
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "underConstrainedEntities": under,
        "redundantConstraints": nominal["redundantConstraints"],
        "missingRoles": [],
        "missingConstraintTypes": [],
        "topologyDiagnostics": topology_diagnostics,
        "diagnostics": diagnostics,
        "cases": cases,
    }
