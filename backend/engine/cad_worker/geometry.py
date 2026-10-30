from __future__ import annotations

from pathlib import Path

from template_core.models import CanonicalPlan, CompileResult, Diagnostic, GeometryMetrics

from .operators.base_entities import _through_penetration_for_shape
from .exporters import write_compile_artifacts
from .operators.body_ops import FaceMap, build_body_with_face_map, resolve_face_support
from .operators.feature_ops import FEATURE_OPERATORS, apply_operation
from .operators.sweep_ops import SweepPathConstructionError, _build_sweep_path_wire, _sketch_sweep
from .postcheck import check_brep


def _locator_field(locator, name: str):
    if isinstance(locator, dict):
        return locator.get(name)
    return getattr(locator, name, None)


def _runtime_locator_diagnostics(plan: CanonicalPlan, face_map: FaceMap) -> list[Diagnostic]:
    """Require every authored manufacturing locator to resolve exactly once."""

    if not plan.operations:
        return []
    body_operation = plan.operations[0]
    body_arguments = body_operation.arguments
    sketch = body_arguments.get("sketch") or {}
    primitive_ids = {
        item.get("id"): item
        for item in sketch.get("primitives", [])
        if item.get("id")
    }
    region_ids = {
        item.get("id"): item
        for item in sketch.get("regions", [])
        if item.get("id")
    }
    body_profile_sketch_id = str(
        body_arguments.get("profileSketchId")
        or sketch.get("id")
        or ""
    )
    diagnostics: list[Diagnostic] = []
    for operation in plan.operations[1:]:
        locator = operation.arguments.get("locator")
        if locator is None:
            continue
        source_id = str(_locator_field(locator, "sourceEntityId") or "")
        operation_id = str(_locator_field(locator, "operationId") or "")
        profile_sketch_id = str(_locator_field(locator, "profileSketchId") or "")
        kind = _locator_field(locator, "kind")
        path = f"operations.{operation.id}.arguments.locator"
        semantic_face_id = str(operation.arguments.get("semanticFaceId") or "<unknown>")

        def add(code: str, message: str) -> None:
            diagnostics.append(Diagnostic(
                severity="error",
                code=code,
                path=path,
                message=f"{semantic_face_id}: {message}",
                suggestion="修复 operationId、profileSketchId 或 sourceEntityId 后重新编译。",
            ))

        if operation_id != body_operation.id:
            add(
                "SEMANTIC_FACE_OPERATION_NOT_FOUND",
                f"来源操作不存在或不是当前基体操作：{operation_id}",
            )
            continue
        if profile_sketch_id != body_profile_sketch_id:
            add(
                "SEMANTIC_FACE_PROFILE_SKETCH_MISMATCH",
                f"来源截面草图不属于基体操作：{profile_sketch_id}",
            )
            continue
        source = primitive_ids.get(source_id) if kind == "profileEdge" else region_ids.get(source_id)
        if source is None:
            add(
                "SEMANTIC_FACE_SOURCE_ENTITY_NOT_FOUND",
                f"来源边不存在：{source_id}" if kind == "profileEdge" else f"来源区域不存在：{source_id}",
            )
            continue
        if kind == "profileEdge" and source.get("type") != "line":
            add(
                "SEMANTIC_FACE_SOURCE_CANNOT_GENERATE_FACE",
                f"来源边无法生成面：当前 CAD 仅支持直线轮廓边（{source_id}）",
            )
            continue
        if kind == "profileRegion" and (
            not source.get("closed") or source.get("operation") != "add"
        ):
            add(
                "SEMANTIC_FACE_SOURCE_CANNOT_GENERATE_FACE",
                f"来源区域无法生成面：必须是闭合加材区域（{source_id}）",
            )
            continue
        try:
            resolve_face_support(face_map, locator)
        except RuntimeError as error:
            message = str(error)
            if "ambiguously" in message or "multiple support" in message:
                add(
                    "SEMANTIC_FACE_SUPPORT_AMBIGUOUS",
                    f"一个定位器命中多个面：{message}",
                )
            else:
                add(
                    "SEMANTIC_FACE_SOURCE_CANNOT_GENERATE_FACE",
                    f"来源边无法生成面：{message}",
                )
    return diagnostics


def execute_plan(plan: CanonicalPlan, output_root: Path, public_prefix: str = "/artifacts") -> CompileResult:
    diagnostics = list(plan.diagnostics)
    if any(item.severity == "error" for item in diagnostics):
        return CompileResult(success=False, inputHash=plan.inputHash, diagnostics=diagnostics)

    job_directory = output_root / plan.inputHash[:16]
    job_directory.mkdir(parents=True, exist_ok=True)
    plan_path = job_directory / "canonical-plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    try:
        shape, face_map = build_body_with_face_map(plan.operations[0])
        locator_diagnostics = _runtime_locator_diagnostics(plan, face_map)
        diagnostics.extend(locator_diagnostics)
        if any(item.severity == "error" for item in locator_diagnostics):
            return CompileResult(
                success=False,
                inputHash=plan.inputHash,
                diagnostics=diagnostics,
            )
        thickness = float(plan.operations[0].arguments.get("thickness", 1))
        depth = max(float(plan.operations[0].arguments.get("depth", thickness)), thickness)
        minimum_penetration = max(
            float(plan.operations[0].arguments.get("length", 0)) * 2
            + thickness * 4,
            depth + thickness * 4,
            thickness * 8,
        )

        for operation in plan.operations[1:]:
            penetration = _through_penetration_for_shape(shape, minimum_penetration)
            if (
                operation.operator in FEATURE_OPERATORS
                and operation.arguments.get("locator") is None
            ):
                semantic_face_id = str(
                    operation.arguments.get("semanticFaceId", "unknown")
                )
                host_frame = str(
                    operation.arguments.get(
                        "hostFrame",
                        operation.arguments.get("hostFace", "negativeY"),
                    )
                )
                diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        code="SEMANTIC_FACE_LOCATOR_MISSING",
                        path=f"operations.{operation.id}.arguments.locator",
                        message=(
                            f"Manufacturing operation {operation.id} on semantic face "
                            f"{semantic_face_id} has no source locator; CAD used the "
                            f"legacy host-frame fallback ({host_frame})."
                        ),
                        suggestion=(
                            "Add a profileEdge or profileRegion locator to the semantic "
                            "face so CAD can resolve its real support face."
                        ),
                    )
                )
            shape = apply_operation(shape, operation, penetration, face_map=face_map)

        valid, properties, solid_count = check_brep(shape)
        if not valid or solid_count != 1 or properties.Mass() <= 0:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="BREP_POSTCHECK_FAILED",
                    path="geometry",
                    message=f"B-Rep 后置检查失败：valid={valid}, solids={solid_count}, volume={properties.Mass():.3f}",
                )
            )
            return CompileResult(success=False, inputHash=plan.inputHash, diagnostics=diagnostics)

        artifacts = write_compile_artifacts(
            shape=shape,
            plan=plan,
            diagnostics=diagnostics,
            job_directory=job_directory,
            plan_path=plan_path,
            public_prefix=public_prefix,
            face_map=face_map,
        )
        return CompileResult(
            success=True,
            inputHash=plan.inputHash,
            diagnostics=diagnostics,
            metrics=GeometryMetrics(
                valid=True,
                volume=round(properties.Mass(), 3),
                solidCount=solid_count,
                operationCount=len(plan.operations),
            ),
            artifacts=artifacts,
        )
    except SweepPathConstructionError as error:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code=error.code,
                path="geometry.sweepPath",
                message=str(error),
                suggestion="检查圆弧参数、路径段连接和连接处解析切线是否连续；若为 line-arc/arc-line，请执行“修复为相切”后重试。",
            )
        )
        return CompileResult(success=False, inputHash=plan.inputHash, diagnostics=diagnostics)
    except Exception as error:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="CAD_EXECUTION_FAILED",
                path="geometry",
                message=str(error),
                suggestion="下载静态计划和诊断信息，定位失败的算子与输入。",
            )
        )
        return CompileResult(success=False, inputHash=plan.inputHash, diagnostics=diagnostics)
