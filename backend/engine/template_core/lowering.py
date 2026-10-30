from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, NamedTuple

from .models import CanonicalPlan, Diagnostic, StaticOperation, TemplateDraft
from .rules import RuleEvaluationError, evaluate_expression, evaluate_template
from .sketch_solver import solve_semantic_sketch
from .sweep_path import ordered_path_points, validate_sweep_path
from .sweep_path_sampling import (
    map_point_to_3d,
    ordered_path_segments_3d,
    sample_ordered_path_data,
)


class _SampledPathPayload(NamedTuple):
    path_points: str
    segment_kinds: list[str]
    segment_geometry_ids: list[str]
    sampled_segments: list[dict[str, Any]]
    segment_tangents: list[tuple[float, float, float] | None]
    station_tangents: list[tuple[float, float, float] | None]
    structured_segments: list[dict[str, Any]]
    exact_geometry_error: str | None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_material_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Remove observation time; audit timestamps are not geometry inputs."""
    snapshot = deepcopy(value)
    provenance = snapshot.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("resolvedAt", None)
    return snapshot


def _resolve_structured_geometry_argument(
    name: str, value: Any, context: dict[str, Any]
) -> Any:
    """Resolve parameter expressions embedded in compact path/station strings."""
    if name not in {"pathPoints", "stations"} or not isinstance(value, str):
        return value
    rows: list[str] = []
    for row in value.split(";"):
        if not row.strip():
            continue
        components = row.split(":")
        expected = 3 if name == "pathPoints" else 2
        if len(components) != expected:
            raise RuleEvaluationError(
                f"{name} must contain {expected} colon-separated expressions per row"
            )
        rows.append(
            ":".join(
                str(float(evaluate_expression(component.strip(), context)))
                for component in components
            )
        )
    return ";".join(rows)


def _sampled_path_payload(path_sketch) -> _SampledPathPayload:
    """Lower exact path segments alongside the compatible sampled payload.

    ``pathPoints`` and its per-chord provenance remain available to older CAD
    workers and as an explicit fallback representation.  The final tuple item
    contains one parameterized arc record (or exact line record) per authored
    path segment for workers capable of constructing a true geometric wire.
    """
    topology = validate_sweep_path(path_sketch)
    ordered = topology.get("ordered", [])
    plane = getattr(path_sketch, "plane", "XY")
    try:
        sampled_data = sample_ordered_path_data(
            path_sketch,
            ordered,
            max_angle_degrees=5.0,
            max_chord_error=0.1,
        )
    except (TypeError, ValueError):
        sampled_data = {"points": [], "segments": []}
    mapped = [
        tuple(float(component) for component in map_point_to_3d(point, plane, xy_as_xz=True))
        for point in sampled_data.get("points", [])
    ]
    segment_records = [dict(item) for item in sampled_data.get("segments", [])]
    segment_kinds = [str(item.get("geometryType", "line")) for item in segment_records]
    segment_geometry_ids = [str(item.get("geometryId", "")) for item in segment_records]
    segment_tangents = [
        tuple(float(component) for component in map_point_to_3d(tangent, plane, xy_as_xz=True))
        if isinstance(tangent, (list, tuple)) and len(tangent) >= 2 else None
        for tangent in (item.get("tangent2d") for item in segment_records)
    ]
    station_tangents = [
        tuple(float(component) for component in map_point_to_3d(tangent, plane, xy_as_xz=True))
        if isinstance(tangent, (list, tuple)) and len(tangent) >= 2 else None
        for tangent in sampled_data.get("stationTangents2d", [])
    ]
    structured_segments: list[dict[str, Any]] = []
    exact_geometry_error: str | None = None
    if topology.get("valid", False):
        try:
            structured_segments = ordered_path_segments_3d(
                path_sketch,
                ordered,
                xy_as_xz=True,
            )
        except (TypeError, ValueError) as error:
            # A valid topology that cannot be represented exactly must not be
            # silently reclassified as a precise polyline path.
            exact_geometry_error = str(error)

    if len(mapped) < 2:
        # Legacy drafts may not have a graph order (for example while a path
        # is still being edited).  Retain the old endpoint best effort.
        points = ordered_path_points(path_sketch)
        mapped = [map_point_to_3d(point, plane, xy_as_xz=True) for point in points]
        segment_kinds = ["line"] * max(0, len(mapped) - 1)
        segment_geometry_ids = [""] * max(0, len(mapped) - 1)
        segment_records = []
        segment_tangents = [None] * max(0, len(mapped) - 1)
        station_tangents = [None] * len(mapped)

    if len(mapped) < 2:
        return _SampledPathPayload("", [], [], [], [], [], [], exact_geometry_error)
    encoded = ";".join(":".join(str(float(component)) for component in point) for point in mapped)
    return _SampledPathPayload(
        encoded,
        segment_kinds,
        segment_geometry_ids,
        segment_records,
        segment_tangents,
        station_tangents,
        structured_segments,
        exact_geometry_error,
    )


def _path_points_from_sketch(path_sketch) -> str:
    """Convert the authored 2D sweep path into the worker's 3D point string."""
    return _sampled_path_payload(path_sketch).path_points


def _precheck(
    draft: TemplateDraft,
    values: dict[str, Any],
    sketch_solution: dict[str, Any] | None = None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    sketch_solution = sketch_solution or solve_semantic_sketch(
        draft,
        {
            key: float(value)
            for key, value in values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        },
    )
    nominal = next((item for item in sketch_solution["cases"] if item["case"] == "nominal"), {"diagnostics": []})
    diagnostics.extend(
        Diagnostic(
            severity=item["severity"],
            code=item["code"],
            path=item["path"],
            message=item["message"],
        )
        for item in [*sketch_solution["topologyDiagnostics"], *nominal["diagnostics"]]
    )
    first_operator = draft.geometryRecipe.operations[0].operator if draft.geometryRecipe.operations else ""
    if first_operator in {"sketch.centerline_thinwall_extrude", "profile.open_profile_tube_extrude"} and draft.sketch.profileMode == "centerlineThinWall" and float(values.get("thickness", 0)) <= 0:
        diagnostics.append(Diagnostic(severity="error", code="THINWALL_THICKNESS_INVALID", path="parameters.thickness", message="中心线薄壁算子的厚度必须大于 0。"))
    if first_operator in {"profile.rectangular_tube_extrude", "profile.open_profile_tube_extrude"} and "depth" in values and (
        float(values.get("depth", 0)) <= float(values.get("thickness", 0)) * 2
    ):
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="TUBE_WALL_INVALID",
                path="parameters.depth",
                message="管材深度必须大于两倍壁厚。",
            )
        )
    return diagnostics


def _referenced_semantic_face_locator_diagnostics(
    draft: TemplateDraft, referenced_face_ids: set[str]
) -> list[Diagnostic]:
    """Validate locators that are about to enter manufacturing operations.

    Stage validation checks every authored semantic face.  Lowering repeats the
    check only for faces used by resolved features so an invalid stable source
    cannot reach CAD, while unrelated legacy/sweep definitions remain outside
    the extrusion-only locator contract.
    """

    if not referenced_face_ids:
        return []

    # Keep the reference rules in one place.  The local import also prevents
    # lowering/stage module initialization from becoming coupled.
    from .stages import _validate_semantic_face_locators

    diagnostics: list[Diagnostic] = []
    faces = {
        face.id: face
        for face in draft.geometryRecipe.semanticFaces
        if face.id in referenced_face_ids and face.locator is not None
    }
    for face_id, face in sorted(faces.items()):
        recipe = draft.geometryRecipe.model_copy(update={"semanticFaces": [face]})
        scoped_draft = draft.model_copy(update={"geometryRecipe": recipe})
        valid, message = _validate_semantic_face_locators(scoped_draft)
        if not valid:
            diagnostics.append(Diagnostic(
                severity="error",
                code="SEMANTIC_FACE_LOCATOR_INVALID",
                path=f"geometryRecipe.semanticFaces.{face_id}.locator",
                message=message,
                suggestion="修复来源操作、截面草图或稳定实体/区域 ID 后重新生成计划。",
            ))
    return diagnostics


def lower_to_plan(
    draft: TemplateDraft,
    material_snapshot: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> CanonicalPlan:
    material_snapshot = stable_material_snapshot(material_snapshot)
    external_context = {
        "material": material_snapshot.get("record", material_snapshot),
        "product": material_snapshot.get("product", {}),
        "component": material_snapshot.get("component", {}),
        "projectZone": material_snapshot.get("projectZone", {}),
        "standard": material_snapshot.get("standard", {}),
    }
    definitions = {item.id: item for item in draft.parameterDefinitions}
    overrides = overrides or {}
    for key, value in overrides.items():
        definition = definitions.get(key)
        if definition is None or not definition.exposed or definition.sourceDefinition.type != "userInput":
            raise ValueError("only exposed user input parameters may be changed")
        if definition.scope not in {"template", "partInstance"}:
            raise ValueError("parameter is not editable for a part instance")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parameter value must be finite")
    evaluation = evaluate_template(
        draft.parameterDefinitions, draft.featureRules,
        overrides=overrides, external_context=external_context, semantic_faces=draft.geometryRecipe.semanticFaces,
        interfaces=draft.interfaces,
    )
    numeric_values = {
        key: float(value)
        for key, value in evaluation.values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    sketch_solution = solve_semantic_sketch(draft, numeric_values)
    diagnostics = _precheck(draft, evaluation.values, sketch_solution)
    if draft.sweepPath is not None and any(item.operator == "solid.sweep" for item in draft.geometryRecipe.operations):
        topology = validate_sweep_path(draft.sweepPath)
        diagnostics.extend(
            Diagnostic(severity=item["severity"], code=item["code"], path=item["path"], message=item["message"])
            for item in topology["diagnostics"]
        )
    sketch_case = next((item for item in sketch_solution["cases"] if item["case"] == "nominal"), None)
    diagnostics.extend(
        Diagnostic(severity=item.severity, code=item.code, path=item.path, message=item.message)
        for item in evaluation.diagnostics
    )
    diagnostics.extend(_referenced_semantic_face_locator_diagnostics(
        draft,
        {feature.semanticFaceId for feature in evaluation.features},
    ))
    operations: list[StaticOperation] = []
    geometry_context = {**external_context, **evaluation.values}
    for definition in draft.geometryRecipe.operations:
        try:
            if not bool(evaluate_expression(definition.conditionExpression, geometry_context)):
                continue
            arguments = dict(definition.arguments)
            if definition.operator == "solid.sweep":
                arguments.update({
                    "profileAnchor": definition.profileAnchor,
                    "orientationMode": definition.orientationMode,
                    "scaleMode": definition.scaleMode,
                    "twistMode": definition.twistMode,
                    "cornerMode": definition.cornerMode,
                })
            sampled_path_payload: _SampledPathPayload | None = None
            if definition.operator == "solid.sweep" and draft.sweepPath is not None:
                path_id = definition.pathSketchId or "path.main"
                if path_id == draft.sweepPath.id or draft.sweepPath.id in definition.sourceRefs:
                    sampled_path_payload = _sampled_path_payload(draft.sweepPath)
            arguments = {
                name: _resolve_structured_geometry_argument(
                    name, value, geometry_context
                )
                for name, value in arguments.items()
            }
            for name, expression in sorted(definition.argumentExpressions.items()):
                arguments[name] = evaluate_expression(expression, geometry_context)
            # A confirmed sweepPath is authoritative over legacy/manual
            # pathPoints arguments.  Keep provenance alongside the sampled
            # points so the worker can distinguish true line corners from arc
            # tessellation stations.
            if sampled_path_payload is not None:
                if sampled_path_payload.exact_geometry_error is not None:
                    diagnostics.append(Diagnostic(
                        severity="error",
                        code="SWEEP_PATH_EXACT_GEOMETRY_LOWERING_FAILED",
                        path=f"geometryRecipe.operations.{definition.id}.pathSegments",
                        message=sampled_path_payload.exact_geometry_error,
                    ))
                if sampled_path_payload.path_points:
                    arguments["pathPoints"] = sampled_path_payload.path_points
                    arguments["pathSegmentKinds"] = sampled_path_payload.segment_kinds
                    arguments["pathSegmentGeometryIds"] = sampled_path_payload.segment_geometry_ids
                    arguments["pathSegments"] = sampled_path_payload.structured_segments
                    arguments["pathSampledSegments"] = sampled_path_payload.sampled_segments
                    arguments["pathSegmentTangents"] = sampled_path_payload.segment_tangents
                    arguments["pathStationTangents"] = sampled_path_payload.station_tangents
                    arguments["pathPlane"] = getattr(draft.sweepPath, "plane", "XY")
            if definition.operator in {"profile.open_profile_tube_extrude", "sketch.region_extrude", "sketch.centerline_thinwall_extrude", "solid.revolve", "solid.sweep", "solid.loft"} and sketch_case is not None:
                profile_sketch_id = definition.profileSketchId or next(
                    (
                        reference
                        for reference in definition.sourceRefs
                        if reference in set(draft.geometryRecipe.sketches)
                    ),
                    draft.geometryRecipe.sketches[0]
                    if len(draft.geometryRecipe.sketches) == 1
                    else None,
                )
                if profile_sketch_id is not None:
                    arguments["profileSketchId"] = profile_sketch_id
                arguments["sketch"] = {
                    "id": profile_sketch_id,
                    "profileMode": draft.sketch.profileMode,
                    "plane": draft.sketch.plane,
                    "primitives": sketch_case["primitives"],
                    "regions": sketch_case["regions"],
                    "topologySignature": sketch_case["topologySignature"],
                    "thickness": evaluation.values.get("thickness"),
                }
            # Carry authored semantic-face declarations with the operation so
            # exporters can publish every stable source, including faces that
            # are not currently referenced by a manufacturing rule.
            supported_locator_operator = definition.operator in {
                "profile.open_profile_tube_extrude",
                "sketch.region_extrude",
            }
            source_entity_ids = {
                entity.id for entity in draft.sketch.entities
            }
            source_region_ids = {
                region.id for region in draft.sketch.regions
            }
            declared_semantic_faces = [
                {
                    "semanticFaceId": face.id,
                    "sourceOperationId": face.sourceOperationId,
                    "hostFrame": face.hostFrame,
                    "locator": (
                        face.locator.model_dump(mode="json")
                        if face.locator is not None
                        else None
                    ),
                }
                for face in draft.geometryRecipe.semanticFaces
                if (
                    supported_locator_operator
                    and face.sourceOperationId == definition.id
                    and (
                        face.locator is None
                        or (
                            face.locator.kind == "profileEdge"
                            and face.locator.sourceEntityId in source_entity_ids
                        )
                        or (
                            face.locator.kind == "profileRegion"
                            and face.locator.sourceEntityId in source_region_ids
                        )
                    )
                )
            ]
            if declared_semantic_faces:
                arguments["semanticFaces"] = declared_semantic_faces
            operations.append(StaticOperation(
                id=definition.id,
                operator=definition.operator,
                arguments=arguments,
                semanticOutputs=definition.semanticOutputs,
            ))
        except RuleEvaluationError as error:
            diagnostics.append(Diagnostic(
                severity="error", code="GEOMETRY_RECIPE_EVALUATION_FAILED",
                path=f"geometryRecipe.operations.{definition.id}", message=str(error),
            ))
    if not operations:
        diagnostics.append(Diagnostic(
            severity="error", code="GEOMETRY_RECIPE_EMPTY", path="geometryRecipe.operations",
            message="基础几何配方未生成任何操作。",
        ))
    rule_operators = {
        "circularHole": "machining.circular_through_hole",
        "straightSlot": "machining.straight_slot_through",
        "rectangularCutout": "machining.rectangular_through_cutout",
        "polygonalCutout": "machining.polygonal_through_cutout",
    }
    for feature in evaluation.features:
        locator = feature.locator.model_dump(mode="json") if feature.locator is not None else None
        operations.append(StaticOperation(
            id=f"cut.{feature.id}",
            operator=rule_operators[feature.featureType],
            arguments={
                **feature.arguments,
                "semanticFaceId": feature.semanticFaceId,
                "locator": locator,
                "resolvedSourceEntityId": feature.resolvedSourceEntityId,
                "hostFrame": feature.hostFace,
                "hostFace": feature.hostFace,
                "uStart": feature.resolvedUStart,
                "uSpan": feature.resolvedUSpan,
                "vStart": feature.resolvedVStart,
                "vSpan": feature.resolvedVSpan,
                "polygonVertices": feature.polygonVertices,
            },
            semanticOutputs=[f"feature.{feature.id}.center", f"feature.{feature.id}.wall"],
        ))

    bounds = sketch_case["bounds"] if sketch_case else {"minimumX": 0, "maximumX": 0}
    minimum_x = float(bounds["minimumX"])
    maximum_x = float(bounds["maximumX"])
    length = float(evaluation.values.get("length", 0))
    for feature in evaluation.features:
        args = feature.arguments
        if feature.featureType == "circularHole":
            dx = dz = float(args.get("diameter", 0))
        elif feature.featureType == "straightSlot":
            dx, dz = float(args.get("width", 0)), float(args.get("length", 0))
        elif feature.featureType == "rectangularCutout":
            dx, dz = float(args.get("width", 0)), float(args.get("height", 0))
        else:
            continue
        x, z = float(args.get("x", 0)), float(args.get("z", 0))
        if dx <= 0 or dz <= 0:
            diagnostics.append(Diagnostic(
                severity="error", code="RESOLVED_FEATURE_SIZE_INVALID",
                path=f"featureRules.{feature.sourceRuleId}", message=f"特征 {feature.id} 的尺寸必须大于零。",
            ))
        elif feature.hostFace in {"negativeY", "positiveY"} and (x - dx / 2 < minimum_x or x + dx / 2 > maximum_x or z - dz / 2 < 0 or z + dz / 2 > length):
            diagnostics.append(Diagnostic(
                severity="error", code="RESOLVED_FEATURE_OUTSIDE_BODY",
                path=f"featureRules.{feature.sourceRuleId}", message=f"特征 {feature.id} 超出基体范围。",
            ))
    hash_source = {
        "geometry": {
            "templateKind": draft.templateKind,
            "geometryPrototypeId": draft.geometryPrototypeId,
            "resolvedParameters": evaluation.values,
            "materialRequirements": [item.model_dump(exclude={"specificBindingId", "reviewed"}) for item in draft.materialRequirements],
            "geometryRecipe": draft.geometryRecipe.model_dump(),
            "sweepPath": draft.sweepPath.model_dump() if draft.sweepPath else None,
            "semanticFaces": [item.model_dump() for item in draft.geometryRecipe.semanticFaces],
            "featureRules": [item.model_dump() for item in draft.featureRules],
            "resolvedFeatures": [item.model_dump() for item in evaluation.features],
            "resolvedInterfaces": [item.model_dump() for item in evaluation.resolvedInterfaces],
            "interfaces": [item.model_dump() for item in draft.interfaces],
        },
        "materialSnapshot": material_snapshot,
        "operations": [operation.model_dump() for operation in operations],
    }
    return CanonicalPlan(
        inputHash=content_hash(hash_source),
        operations=operations,
        materialSnapshot=material_snapshot,
        diagnostics=diagnostics,
    )
