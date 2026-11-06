from __future__ import annotations

from typing import Any

from .models import CompileResult, StageCheck, StageName, StageValidation, TemplateDraft
from .stage1 import template_info_fingerprint, validate_template_info
from .rules import RuleEvaluationError, evaluate_template, expression_names, parameter_evaluation_order, resolve_parameters
from .material import effective_thickness_domain, material_requirement_mismatches
from .sketch_solver import solve_semantic_sketch
from .sweep_path import validate_sweep_path


STAGE_ORDER: tuple[StageName, ...] = ("templateInfo", "material", "baseSketch", "features", "variants", "review", "admission")


def _collect_expression_names(expressions: list[str]) -> tuple[set[str], bool]:
    names: set[str] = set()
    syntax_valid = True
    for expression in expressions:
        try:
            names |= expression_names(expression)
        except RuleEvaluationError:
            syntax_valid = False
    return names, syntax_valid


def _parameter_aliases(draft: TemplateDraft) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for parameter in draft.parameterDefinitions:
        names = {
            parameter.id,
            parameter.label,
            parameter.displayName or "",
            *(parameter.aliases or []),
        }
        for alias in names:
            alias = alias.strip()
            if alias and alias != parameter.id:
                aliases[alias] = parameter.id
    return aliases


def _expression_alias_violations(
    expressions: list[tuple[str, str]],
    draft: TemplateDraft,
) -> tuple[list[tuple[str, str, str]], bool]:
    alias_map = _parameter_aliases(draft)
    parameter_ids = {item.id for item in draft.parameterDefinitions}
    violations: list[tuple[str, str, str]] = []
    syntax_valid = True
    for path, expression in expressions:
        try:
            names = expression_names(expression)
        except RuleEvaluationError:
            syntax_valid = False
            continue
        for name in sorted(names):
            if name in parameter_ids:
                continue
            parameter_id = alias_map.get(name)
            if parameter_id:
                violations.append((path, name, parameter_id))
    return violations, syntax_valid


def _alias_violation_message(violations: list[tuple[str, str, str]]) -> str:
    items = [f"{path} 使用了“{name}”，请改为参数 ID “{parameter_id}”" for path, name, parameter_id in violations]
    return "；".join(items)


def _geometry_parameter_references(draft: TemplateDraft) -> tuple[set[str], bool]:
    expressions: list[str] = []
    for operation in draft.geometryRecipe.operations:
        expressions.extend([*operation.argumentExpressions.values(), operation.conditionExpression])
        for structured_name in ("pathPoints", "stations"):
            structured = operation.arguments.get(structured_name)
            if isinstance(structured, str):
                expressions.extend(
                    component.strip()
                    for row in structured.split(";")
                    for component in row.split(":")
                    if component.strip()
                )
    for face in draft.geometryRecipe.semanticFaces:
        expressions.extend([
            face.uStartExpression,
            face.uSpanExpression,
            face.vStartExpression,
            face.vSpanExpression,
        ])
    return _collect_expression_names(expressions)


def _sweep_path_validation(draft: TemplateDraft) -> dict[str, Any]:
    path = draft.sweepPath
    if path is None:
        return {"valid": False, "diagnostics": [{"severity": "error", "code": "SWEEP_PATH_EMPTY", "path": "sweepPath", "message": "未配置扫掠路径。"}], "ordered": [], "startEndpointRef": None}
    result = validate_sweep_path(path)
    if path.status not in {"valid", "confirmed"}:
        result["valid"] = False
        result["diagnostics"].append({"severity": "error", "code": "SWEEP_PATH_NOT_CONFIRMED", "path": "sweepPath.status", "message": "扫掠路径必须先确认后才能进入几何阶段。"})
    return result


def _sweep_path_is_valid(draft: TemplateDraft) -> bool:
    return bool(_sweep_path_validation(draft)["valid"])


def _validate_geometry_sketch_path_references(draft: TemplateDraft) -> tuple[bool, str]:
    sweep_operations = [item for item in draft.geometryRecipe.operations if item.operator == "solid.sweep"]
    if not sweep_operations:
        return True, "无扫掠操作"
    sketches = set(draft.geometryRecipe.sketches)
    paths = set(draft.geometryRecipe.paths)
    profile_id = "sketch.section.main"
    path_id = "path.main"
    for operation in sweep_operations:
        if operation.profileSketchId != profile_id:
            return False, f"{operation.id} 必须明确引用截面草图 {profile_id}"
        if operation.pathSketchId != path_id:
            return False, f"{operation.id} 必须明确引用扫掠路径 {path_id}"
        if profile_id not in operation.sourceRefs or path_id not in operation.sourceRefs or set(operation.sourceRefs) != {profile_id, path_id}:
            return False, f"{operation.id} 的 sourceRefs 必须且只能同时包含 {profile_id} 和 {path_id}"
    if profile_id not in sketches:
        return False, f"geometryRecipe.sketches 缺少 {profile_id}"
    if draft.sweepPath is None or draft.sweepPath.id != path_id or path_id not in paths:
        return False, f"geometryRecipe.paths 缺少 {path_id}"
    return True, "截面草图与扫掠路径引用完整"


def _validate_semantic_face_locators(draft: TemplateDraft) -> tuple[bool, str]:
    """Check authored semantic-face sources without consulting CAD topology.

    A locator is deliberately optional so drafts written before the locator
    contract remain readable and valid.  When present, its source ID is
    checked against the semantic sketch entity/region collections, while its
    operation and profile sketch IDs are checked against authored recipe
    references.  No B-Rep face ordering is involved here.
    """

    operations = {item.id: item for item in draft.geometryRecipe.operations}
    recipe_sketches = set(draft.geometryRecipe.sketches)
    entities_by_id: dict[str, list[Any]] = {}
    for entity in draft.sketch.entities:
        entities_by_id.setdefault(entity.id, []).append(entity)
    regions_by_id: dict[str, list[Any]] = {}
    for region in draft.sketch.regions:
        regions_by_id.setdefault(region.id, []).append(region)
    supported_operators = {
        "profile.open_profile_tube_extrude",
        "sketch.region_extrude",
    }
    errors: list[str] = []

    for face in draft.geometryRecipe.semanticFaces:
        locator = face.locator
        if locator is None:
            continue
        path = f"geometryRecipe.semanticFaces.{face.id}.locator"
        operation = operations.get(locator.operationId)
        if operation is None:
            errors.append(f"{path}.operationId 引用了不存在的几何操作 {locator.operationId}")
            continue
        if locator.kind not in {"profileEdge", "profileRegion"}:
            errors.append(
                f"{path}.kind 必须是 profileEdge 或 profileRegion：{locator.kind}"
            )
            continue
        if operation.operator not in supported_operators:
            errors.append(
                f"{path}.operationId 仅支持来源于拉伸算子 "
                "profile.open_profile_tube_extrude 或 sketch.region_extrude"
            )

        if locator.profileSketchId not in recipe_sketches:
            errors.append(
                f"{path}.profileSketchId 引用了未声明的截面草图 {locator.profileSketchId}"
            )
        if operation.profileSketchId is not None and operation.profileSketchId != locator.profileSketchId:
            errors.append(
                f"{path}.profileSketchId 与几何操作 {operation.id} 的 profileSketchId 不一致"
            )
        if locator.profileSketchId not in set(operation.sourceRefs):
            errors.append(
                f"{path}.profileSketchId 必须被几何操作 {operation.id} 的 sourceRefs 引用"
            )
        if face.sourceOperationId != locator.operationId:
            errors.append(
                f"{path}.operationId 与语义面 {face.id} 的 sourceOperationId 不一致"
            )

        if locator.kind == "profileEdge":
            entities = entities_by_id.get(locator.sourceEntityId, [])
            if not entities:
                errors.append(
                    f"{path}.sourceEntityId 必须引用已有草图实体：{locator.sourceEntityId}"
                )
            elif len(entities) != 1:
                errors.append(
                    f"{path}.sourceEntityId 必须唯一引用一个草图实体：{locator.sourceEntityId}"
                )
            else:
                entity = entities[0]
                if entity.construction or entity.geometryType == "point":
                    errors.append(
                        f"{path}.sourceEntityId 必须引用非构造且非点的截面轮廓边：{locator.sourceEntityId}"
                    )
        else:
            regions = regions_by_id.get(locator.sourceEntityId, [])
            if not regions:
                errors.append(
                    f"{path}.sourceEntityId 必须引用已有草图区域：{locator.sourceEntityId}"
                )
            elif len(regions) != 1:
                errors.append(
                    f"{path}.sourceEntityId 必须唯一引用一个草图区域：{locator.sourceEntityId}"
                )
            elif not regions[0].closed:
                errors.append(
                    f"{path}.sourceEntityId 必须引用闭合截面区域：{locator.sourceEntityId}"
                )

    return not errors, "；".join(errors) or "语义面来源定位器引用完整"


def _validate_semantic_face_locator_cases(
    draft: TemplateDraft,
    sketch_solution: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Check locator sources in the minimum, nominal and maximum sketch cases."""

    solution = sketch_solution or solve_semantic_sketch(draft)
    operations = {item.id: item for item in draft.geometryRecipe.operations}
    recipe_sketches = set(draft.geometryRecipe.sketches)
    errors: list[str] = []
    supported_operators = {
        "profile.open_profile_tube_extrude",
        "sketch.region_extrude",
    }
    for case in solution.get("cases", []):
        case_name = case.get("case", "unknown")
        primitives = {
            item.get("id"): item
            for item in case.get("primitives", [])
            if item.get("id")
        }
        regions = {
            item.get("id"): item
            for item in case.get("regions", [])
            if item.get("id")
        }
        boundary_uses: dict[str, int] = {}
        for region in case.get("regions", []):
            for source_id in region.get("boundaryRefs", []):
                boundary_uses[source_id] = boundary_uses.get(source_id, 0) + 1
        for face in draft.geometryRecipe.semanticFaces:
            locator = face.locator
            if locator is None:
                continue
            path = f"geometryRecipe.semanticFaces.{face.id}.locator"
            operation = operations.get(locator.operationId)
            if operation is None:
                errors.append(
                    f"[{case_name}] {path}.operationId 来源操作不存在：{locator.operationId}"
                )
                continue
            if locator.kind not in {"profileEdge", "profileRegion"}:
                errors.append(
                    f"[{case_name}] {path}.kind 必须是 profileEdge 或 profileRegion："
                    f"{locator.kind}"
                )
                continue
            if operation.operator not in supported_operators:
                errors.append(
                    f"[{case_name}] {path}.operationId 来源操作无法解析："
                    f"仅支持拉伸算子（{operation.operator}）"
                )
                continue
            if (
                locator.profileSketchId not in recipe_sketches
            ):
                errors.append(
                    f"[{case_name}] {path}.profileSketchId 来源截面草图不存在："
                    f"{locator.profileSketchId}"
                )
                continue
            if operation.profileSketchId and operation.profileSketchId != locator.profileSketchId:
                errors.append(
                    f"[{case_name}] {path}.profileSketchId 与来源操作不一致："
                    f"{locator.profileSketchId}"
                )
                continue
            if locator.profileSketchId not in set(operation.sourceRefs):
                errors.append(
                    f"[{case_name}] {path}.profileSketchId 未被来源操作引用："
                    f"{locator.profileSketchId}"
                )
                continue
            if locator.kind == "profileEdge":
                primitive = primitives.get(locator.sourceEntityId)
                if primitive is None or primitive.get("construction"):
                    errors.append(
                        f"[{case_name}] {path}.sourceEntityId 来源边不存在：{locator.sourceEntityId}"
                    )
                    continue
                if primitive.get("type") != "line":
                    errors.append(
                        f"[{case_name}] {path}.sourceEntityId 来源边无法生成面："
                        f"当前仅支持直线轮廓边（{locator.sourceEntityId}）"
                    )
                    continue
                if boundary_uses.get(locator.sourceEntityId, 0) == 0:
                    errors.append(
                        f"[{case_name}] {path}.sourceEntityId 来源边无法生成面："
                        f"未参与闭合材料区域（{locator.sourceEntityId}）"
                    )
                elif boundary_uses[locator.sourceEntityId] > 1:
                    errors.append(
                        f"[{case_name}] {path} 一个定位器命中多个面："
                        f"{locator.sourceEntityId} 出现在多个区域边界"
                    )
            else:
                region = regions.get(locator.sourceEntityId)
                if region is None:
                    errors.append(
                        f"[{case_name}] {path}.sourceEntityId 来源区域不存在：{locator.sourceEntityId}"
                    )
                elif not region.get("closed") or region.get("operation") != "add":
                    errors.append(
                        f"[{case_name}] {path}.sourceEntityId 来源区域无法生成面："
                        f"必须是闭合加材区域（{locator.sourceEntityId}）"
                    )

    return not errors, "；".join(errors) or "最小、标称和最大工况的语义面来源均可唯一解析"


def sweep_preview_admission(draft: TemplateDraft, material_snapshot: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return blocking diagnostics before any 3-D preview/CAD work starts."""
    operations = [item for item in draft.geometryRecipe.operations if item.operator == "solid.sweep"]
    if not operations:
        return []
    missing: list[dict[str, str]] = []
    entities = [item for item in draft.sketch.entities if not item.construction]
    entity_ids = {item.id for item in entities}
    if not entities or not draft.sketch.regions or any(not region.closed or not set(region.boundaryRefs) <= entity_ids for region in draft.sketch.regions):
        missing.append({"code": "SWEEP_PREVIEW_PROFILE_INVALID", "path": "sketch", "message": "三维预览需要有效且闭合的截面草图。"})
    path_result = _sweep_path_validation(draft)
    if draft.sweepPath is None or draft.sweepPath.status != "confirmed":
        missing.append({"code": "SWEEP_PREVIEW_PATH_UNCONFIRMED", "path": "sweepPath.status", "message": "三维预览需要已确认的扫掠路径。"})
    for item in path_result.get("diagnostics", []):
        if item["code"] in {"SWEEP_PATH_DISCONNECTED", "SWEEP_PATH_BRANCH", "SWEEP_PATH_SELF_INTERSECTION", "SWEEP_PATH_DUPLICATE_SEGMENT", "SWEEP_PATH_ILLEGAL_GEOMETRY", "SWEEP_PATH_ZERO_LENGTH", "SWEEP_PATH_GEOMETRY_INVALID", "SWEEP_PATH_START_UNDEFINED"}:
            missing.append({"code": item["code"], "path": item["path"], "message": item["message"]})
    if not path_result.get("startEndpointRef"):
        missing.append({"code": "SWEEP_PREVIEW_START_MISSING", "path": "sweepPath.startEndpointRef", "message": "三维预览需要确定扫掠路径起点。"})
    reference_ok, reference_message = _validate_geometry_sketch_path_references(draft)
    if not reference_ok:
        missing.append({"code": "SWEEP_PREVIEW_REFERENCES_INVALID", "path": "geometryRecipe", "message": reference_message})
    for operation in operations:
        if operation.profileAnchor != "sketch.origin" or operation.orientationMode not in {"followPath", "fixedWorld", "minimumTwist"} or operation.scaleMode != "constant" or operation.twistMode != "none" or operation.cornerMode != "right":
            missing.append({"code": "SWEEP_PREVIEW_CONFIGURATION_INVALID", "path": f"geometryRecipe.operations.{operation.id}", "message": "扫掠配置必须使用草图原点、最小扭转、恒定缩放、无扭转和 RightCorner。"})
    if not draft.parameterDefinitions:
        missing.append({"code": "SWEEP_PREVIEW_PARAMETERS_MISSING", "path": "parameterDefinitions", "message": "三维预览缺少生成参数。"})
    record = (material_snapshot or {}).get("record", material_snapshot or {})
    if not isinstance(record, dict) or not record.get("code"):
        missing.append({"code": "SWEEP_PREVIEW_MATERIAL_MISSING", "path": "materialSnapshot", "message": "三维预览缺少完整材料数据。"})
    return missing


def _validation(stage: StageName, checks: list[StageCheck]) -> StageValidation:
    blocking = [item for item in checks if item.severity == "error"]
    progress = round(sum(item.passed for item in blocking) / len(blocking) * 100) if blocking else 100
    return StageValidation(stage=stage, complete=all(item.passed for item in blocking), progress=progress, checks=checks)


def validate_material(draft: TemplateDraft, sample_contexts: list[dict[str, Any]] | None = None) -> StageValidation:
    requirements = draft.materialRequirements
    requirement_complete = bool(requirements and all(item.supplyForm and item.reviewed for item in requirements))
    requires_specific = any(item.selectionMode == "specificRecord" for item in requirements)
    specific_resolved = not requires_specific or all(item.specificBindingId for item in requirements if item.selectionMode == "specificRecord")
    contexts = sample_contexts or []
    samples_resolved = len(contexts) == len(draft.materialValidationSamples)
    thickness = requirements[0].thickness if requirements else None
    thickness_domain = effective_thickness_domain(requirements[0]) if requirements else {"values": [], "minimum": None, "maximum": None, "empty": False}
    boundary_matrix_required = bool(thickness and (len(thickness_domain["values"]) > 1 or (thickness_domain["minimum"] is not None and thickness_domain["maximum"] is not None and thickness_domain["minimum"] != thickness_domain["maximum"])))
    required_roles = {"nominal", "minimum", "maximum"} if boundary_matrix_required else {"nominal"}
    samples_by_role = {item.role: item for item in draft.materialValidationSamples}
    required_samples_complete = required_roles <= set(samples_by_role) and all(samples_by_role[role].reviewed for role in required_roles)
    mismatches = [reason for context in contexts for reason in context.get("mismatches", [])]
    contexts_by_sample = {item.get("sampleId"): item for item in contexts}
    boundary_mismatches: list[str] = []
    if thickness:
        lower = thickness_domain["minimum"]
        upper = thickness_domain["maximum"]
        for role, expected, label in (("minimum", lower, "最小"), ("maximum", upper, "最大")):
            sample = samples_by_role.get(role)
            context = contexts_by_sample.get(sample.id) if sample else None
            actual = context.get("material", {}).get("thickness") if context else None
            if sample and expected is not None and (actual is None or abs(float(actual) - float(expected)) > 1e-9):
                boundary_mismatches.append(f"{label}边界样例厚度应为 {expected} mm，当前为 {actual if actual is not None else '未定义'}")
    if requires_specific:
        specific_ids = {item.specificBindingId for item in requirements if item.selectionMode == "specificRecord"}
        if any(sample.bindingId not in specific_ids for sample in draft.materialValidationSamples):
            mismatches.append("验证样例不是模板指定的唯一材料记录")
    drifted = any(bool(context.get("provenance", {}).get("drifted")) for context in contexts)
    thickness_parameter = next((item for item in draft.parameterDefinitions if thickness and item.id == thickness.parameterId), None)
    parameter_source = thickness_parameter.sourceDefinition if thickness_parameter else None
    parameter_bound = bool(thickness_parameter and parameter_source and parameter_source.type == "materialProperty" and parameter_source.reference == "material.thickness")
    parameter_covers_domain = bool(thickness_parameter)
    if thickness_parameter:
        lower, upper = thickness_domain["minimum"], thickness_domain["maximum"]
        if lower is not None and thickness_parameter.minimum is not None and lower < thickness_parameter.minimum:
            parameter_covers_domain = False
        if upper is not None and thickness_parameter.maximum is not None and upper > thickness_parameter.maximum:
            parameter_covers_domain = False
        if thickness_domain["values"] and thickness_parameter.allowedValues and not set(thickness_domain["values"]) <= {float(value) for value in thickness_parameter.allowedValues if isinstance(value, (int, float)) and not isinstance(value, bool)}:
            parameter_covers_domain = False
    blank_preparation_ok = draft.blank.preparationMode == "sameAsSupply" or any(item != "none" for item in draft.blank.preparationProcesses)
    declared_parameters = {item.id for item in draft.parameterDefinitions}
    blank_expressions_valid = True
    blank_expression_unknown: set[str] = set()
    for expression in (draft.blank.lengthExpression, draft.blank.widthExpression, draft.blank.thicknessExpression):
        try:
            blank_expression_unknown |= expression_names(expression) - declared_parameters
        except RuleEvaluationError:
            blank_expressions_valid = False
    blank_alias_violations, _ = _expression_alias_violations(
        [
            ("blank.lengthExpression", draft.blank.lengthExpression),
            ("blank.widthExpression", draft.blank.widthExpression),
            ("blank.thicknessExpression", draft.blank.thicknessExpression),
        ],
        draft,
    )
    checks = [
        StageCheck(id="material-requirement", label="材料需求已定义", passed=requirement_complete, severity="error", path="materialRequirements", message="请定义材料类别、厚度约束并人工确认。"),
        StageCheck(id="thickness-domain", label="有效厚度域可求解", passed=not thickness_domain["empty"], severity="error", path="materialRequirements.0.thickness", message="允许厚度值与厚度上下限没有交集，请调整厚度约束。"),
        StageCheck(id="thickness-parameter-binding", label="厚度参数由材料驱动", passed=parameter_bound, severity="error", path="parameterDefinitions", message="厚度驱动参数必须存在，并引用 material.thickness。"),
        StageCheck(id="thickness-parameter-domain", label="参数契约覆盖有效厚度域", passed=parameter_covers_domain, severity="error", path="parameterDefinitions", message="厚度参数自身的允许值或上下限排除了材料有效厚度域，请统一参数契约。"),
        StageCheck(id="specific-material", label="指定材料记录已解析", passed=specific_resolved, severity="error", path="materialRequirements", message="选择具体材料模式时必须绑定材料记录。"),
        StageCheck(id="validation-samples", label="必需材料样例已确认", passed=required_samples_complete and samples_resolved, severity="error", path="materialValidationSamples", message=f"请添加并确认：{'、'.join(sorted(required_roles))}材料样例。"),
        StageCheck(id="sample-compatibility", label="验证样例满足材料要求", passed=not mismatches, severity="error", path="materialValidationSamples", message=f"样例与材料要求不匹配：{'；'.join(sorted(set(mismatches))) or '无'}。"),
        StageCheck(id="boundary-sample-thickness", label="材料样例覆盖厚度边界", passed=not boundary_mismatches, severity="error", path="materialValidationSamples", message="；".join(boundary_mismatches) or "最小与最大材料样例已覆盖厚度边界。"),
        StageCheck(id="blank-form", label="制造起始毛坯已定义", passed=bool(draft.blank.form), severity="error", path="blank.form", message="请选择制造过程开始时的毛坯形态。"),
        StageCheck(id="blank-preparation", label="毛坯准备关系完整", passed=blank_preparation_ok, severity="error", path="blank.preparationProcesses", message="选择独立毛坯时必须定义至少一道准备工序。"),
        StageCheck(id="blank-expressions", label="毛坯尺寸表达式有效", passed=blank_expressions_valid and not blank_expression_unknown, severity="error", path="blank", message=f"毛坯尺寸只能引用已声明参数：{', '.join(sorted(blank_expression_unknown)) or '请检查表达式语法'}。"),
        StageCheck(id="blank-expression-id-only", label="毛坯表达式仅使用参数ID", passed=not blank_alias_violations, severity="error", path="blank", message=_alias_violation_message(blank_alias_violations) or "毛坯表达式仅可使用参数稳定 ID。"),
        StageCheck(id="route", label="制造路线已定义", passed=bool(draft.blank.manufacturingRoute), severity="error", path="blank.manufacturingRoute", message="请选择主要制造路线。"),
        StageCheck(id="material-drift", label="材料来源无漂移", passed=not drifted, severity="warning", path="materialValidationSamples", message="引用样例材料已变化；发布前应重新确认或改为冻结快照。"),
    ]
    return _validation("material", checks)


def validate_base_sketch(
    draft: TemplateDraft,
    sketch_solution: dict[str, Any] | None = None,
) -> StageValidation:
    required, expression_syntax_valid = _geometry_parameter_references(draft)
    known_parameters = {item.id for item in draft.parameterDefinitions}
    expressions_valid = expression_syntax_valid and required <= known_parameters
    missing = required - known_parameters
    base_sketch_expressions: list[tuple[str, str]] = []
    for operation in draft.geometryRecipe.operations:
        for name, expression in operation.argumentExpressions.items():
            base_sketch_expressions.append((f"geometryRecipe.operations.{operation.id}.{name}", expression))
        base_sketch_expressions.append((f"geometryRecipe.operations.{operation.id}.conditionExpression", operation.conditionExpression))
        for structured_name in ("pathPoints", "stations"):
            structured = operation.arguments.get(structured_name)
            if isinstance(structured, str):
                base_sketch_expressions.append((f"geometryRecipe.operations.{operation.id}.{structured_name}", structured))
    for face in draft.geometryRecipe.semanticFaces:
        base_sketch_expressions.extend([
            (f"geometryRecipe.semanticFaces.{face.id}.uStartExpression", face.uStartExpression),
            (f"geometryRecipe.semanticFaces.{face.id}.uSpanExpression", face.uSpanExpression),
            (f"geometryRecipe.semanticFaces.{face.id}.vStartExpression", face.vStartExpression),
            (f"geometryRecipe.semanticFaces.{face.id}.vSpanExpression", face.vSpanExpression),
        ])
    for constraint in draft.sketch.constraints:
        if constraint.expression:
            base_sketch_expressions.append((f"sketch.constraints.{constraint.id}.expression", constraint.expression))
    base_sketch_alias_violations, _ = _expression_alias_violations(base_sketch_expressions, draft)
    source_ok = (
        draft.sketch.acquisitionMethod not in {"imported", "reused"}
        or bool(draft.sketch.sourceAttachmentId or draft.sketch.sourceProfileId)
    )
    conversion_ok = draft.sketch.acquisitionMethod not in {"imported", "reused"} or draft.sketch.conversionReviewed
    entity_ids = {item.id for item in draft.sketch.entities}
    semantic_entities_ok = bool(entity_ids) and all(item.role and set(item.parameterRefs) <= known_parameters for item in draft.sketch.entities)
    constraints_ok = bool(draft.sketch.constraints) and all(set(item.entityRefs) <= entity_ids for item in draft.sketch.constraints)
    regions_ok = (
        (
            bool(draft.sketch.regions)
            and all(item.closed and set(item.boundaryRefs) <= entity_ids for item in draft.sketch.regions)
        )
        or bool([item for item in draft.sketch.entities if not item.construction])
        if draft.sketch.profileMode == "centerlineThinWall"
        else bool(draft.sketch.regions) and all(item.closed and set(item.boundaryRefs) <= entity_ids for item in draft.sketch.regions)
    )
    sketch_solution = sketch_solution or solve_semantic_sketch(draft)
    supported_operators = {
        "profile.open_profile_tube_extrude",
        "sketch.region_extrude", "sketch.centerline_thinwall_extrude",
        "sheet.blank_extrude", "profile.rectangular_tube_extrude",
        "solid.revolve", "solid.sweep", "solid.loft", "sheet.bend",
    }
    operators_supported = all(
        operation.operator in supported_operators
        for operation in draft.geometryRecipe.operations
    )
    operator_inputs_ok = True
    operator_input_messages: list[str] = []
    reference_ok, reference_message = _validate_geometry_sketch_path_references(draft)
    path_validation = _sweep_path_validation(draft) if any(item.operator == "solid.sweep" for item in draft.geometryRecipe.operations) and draft.sweepPath is not None else {"valid": True, "diagnostics": []}
    for operation in draft.geometryRecipe.operations:
        keys = set(operation.arguments) | set(operation.argumentExpressions)
        required_keys = {
            "solid.revolve": {"angleDegrees"},
            "solid.sweep": {"pathPoints"},
            "solid.loft": {"stations"},
            "sheet.bend": {"length", "width", "thickness", "bendPosition", "bendAngleDegrees", "insideRadius", "kFactor"},
        }.get(operation.operator, set())
        missing_keys = required_keys - keys
        if operation.operator == "solid.sweep" and draft.sweepPath is not None:
            path_id = operation.pathSketchId or "path.main"
            if path_id == draft.sweepPath.id or draft.sweepPath.id in operation.sourceRefs:
                missing_keys.discard("pathPoints")
                if not _sweep_path_is_valid(draft):
                    operator_input_messages.append(f"{operation.id} 的扫掠路径尚未确认或无效")
        if missing_keys:
            operator_inputs_ok = False
            operator_input_messages.append(f"{operation.id} 缺少 {', '.join(sorted(missing_keys))}")
    sweep_path_ok = True
    sweep_path_message = "未配置扫掠路径"
    sweep_operations = [item for item in draft.geometryRecipe.operations if item.operator == "solid.sweep"]
    if sweep_operations:
        if draft.sweepPath is not None:
            sweep_path_ok = bool(path_validation["valid"])
            sweep_path_message = "扫掠路径已确认" if sweep_path_ok else "；".join(item["message"] for item in path_validation["diagnostics"]) or "扫掠路径拓扑无效"
        else:
            sweep_path_ok = all("pathPoints" in (set(item.arguments) | set(item.argumentExpressions)) for item in sweep_operations)
            sweep_path_message = "使用算子内置路径点" if sweep_path_ok else "路径扫掠需要一条已确认的扫掠路径"
    locator_ok, locator_message = _validate_semantic_face_locators(draft)
    locator_cases_ok, locator_cases_message = _validate_semantic_face_locator_cases(
        draft, sketch_solution
    )
    checks = [
        StageCheck(id="driving-parameters", label="几何驱动参数完整", passed=expressions_valid, severity="error", path="geometryRecipe.operations", message=f"几何配方表达式无效，或引用了未声明参数：{', '.join(sorted(missing)) or '请检查表达式语法'}。草图驱动参数只需包含轮廓自身使用的参数。"),
        StageCheck(id="semantic-entities", label="语义图元契约完整", passed=semantic_entities_ok, severity="error", path="sketch.entities", message="请为草图图元定义稳定语义名称，并只引用已声明参数。"),
        StageCheck(id="semantic-constraints", label="草图约束引用有效", passed=constraints_ok, severity="error", path="sketch.constraints", message="草图必须包含有效约束，且约束只能引用已有语义图元。"),
        StageCheck(id="semantic-regions", label="截面材料区域已定义", passed=regions_ok, severity="error", path="sketch.regions", message="闭合轮廓模式需定义材料区域；中心线薄壁模式需定义连续的直线／圆弧路径。"),
        StageCheck(id="sketch-solver", label="语义草图可求解", passed=sketch_solution["valid"], severity="error", path="sketch", message="；".join(item["message"] for item in sketch_solution["diagnostics"] if item["severity"] == "error") or "最小、标称和最大草图工况均可求解。"),
        StageCheck(id="sketch-degrees-of-freedom", label="语义草图完全约束", passed=sketch_solution["fullyConstrained"], severity="error", path="sketch.constraints", message=f"当前剩余自由度：{sketch_solution['degreesOfFreedom']}；请补齐语义段或约束。"),
        StageCheck(id="constraints-reviewed", label="约束已人工确认", passed=draft.sketch.constraintsReviewed, severity="error", path="sketch.constraintsReviewed", message="请确认截面尺寸、相切与闭合关系。"),
        StageCheck(id="sketch-source", label="草图来源可追溯", passed=source_ok, severity="error", path="sketch.sourceAttachmentId", message="导入转换必须关联来源附件；受控复用必须关联来源截面。"),
        StageCheck(id="sketch-conversion", label="来源转换已复核", passed=conversion_ok, severity="error", path="sketch.conversionReviewed", message="导入或复用的轮廓必须完成语义转换并人工复核。"),
        StageCheck(id="geometry-recipe", label="基础几何配方已建立", passed=bool(draft.geometryRecipe.operations), severity="error", path="geometryRecipe.operations", message="基础几何必须至少包含一个拉伸、旋转、扫掠、放样、钣金或派生操作。"),
        StageCheck(id="geometry-operators-supported", label="几何算子均已实现", passed=operators_supported, severity="error", path="geometryRecipe.operations", message="当前配方包含CAD内核尚未实现的算子。"),
        StageCheck(id="geometry-operator-inputs", label="几何算子输入完整", passed=operator_inputs_ok, severity="error", path="geometryRecipe.operations", message="；".join(operator_input_messages) or "算子输入完整。"),
        StageCheck(id="geometry-sketch-path-references", label="截面与路径草图引用完整", passed=reference_ok, severity="error", path="geometryRecipe", message=reference_message),
        StageCheck(id="semantic-face-locators", label="语义面来源定位器引用有效", passed=locator_ok, severity="error", path="geometryRecipe.semanticFaces", message=locator_message),
        StageCheck(id="semantic-face-locator-cases", label="语义面来源三工况可解析", passed=locator_cases_ok, severity="error", path="geometryRecipe.semanticFaces", message=locator_cases_message),
        StageCheck(id="sweep-path", label="扫掠路径已定义", passed=sweep_path_ok, severity="error", path="sweepPath", message=sweep_path_message),
        StageCheck(id="geometry-reviewed", label="基础几何配方已确认", passed=draft.geometryRecipe.reviewed, severity="error", path="geometryRecipe.reviewed", message="请人工确认基础几何的构造方式和语义输出。"),
        StageCheck(id="geometry-expressions-id-only", label="几何表达式仅使用参数ID", passed=not base_sketch_alias_violations, severity="error", path="geometryRecipe", message=_alias_violation_message(base_sketch_alias_violations) or "几何表达式仅可使用参数稳定 ID。"),
    ]
    return _validation("baseSketch", checks)


def validate_features(
    draft: TemplateDraft,
    sketch_solution: dict[str, Any] | None = None,
) -> StageValidation:
    feature_expressions: list[tuple[str, str]] = []
    for parameter in draft.parameterDefinitions:
        source = parameter.sourceDefinition
        if source and source.type == "formula" and source.expression:
            feature_expressions.append((f"parameterDefinitions.{parameter.id}.sourceDefinition.expression", source.expression))
        if source and source.type == "lookup" and source.reference:
            feature_expressions.append((f"parameterDefinitions.{parameter.id}.sourceDefinition.reference", source.reference))
    for rule in draft.featureRules:
        feature_expressions.extend([
            (f"featureRules.{rule.id}.conditionExpression", rule.conditionExpression),
            (f"featureRules.{rule.id}.countExpression", rule.countExpression),
        ])
        for name, expression in rule.argumentExpressions.items():
            feature_expressions.append((f"featureRules.{rule.id}.argumentExpressions.{name}", expression))
        feature_expressions.extend([
            (f"featureRules.{rule.id}.placement.pitchExpression", rule.placement.pitchExpression),
            (f"featureRules.{rule.id}.placement.startMarginExpression", rule.placement.startMarginExpression),
            (f"featureRules.{rule.id}.placement.endMarginExpression", rule.placement.endMarginExpression),
            (f"featureRules.{rule.id}.placement.maximumPitchExpression", rule.placement.maximumPitchExpression),
        ])
        for index, vertex in enumerate(rule.polygonVertices, start=1):
            feature_expressions.extend([
                (f"featureRules.{rule.id}.polygonVertices.{index}.uExpression", vertex.uExpression),
                (f"featureRules.{rule.id}.polygonVertices.{index}.vExpression", vertex.vExpression),
            ])
    feature_alias_violations, _ = _expression_alias_violations(feature_expressions, draft)
    evaluation = evaluate_template(
        draft.parameterDefinitions, draft.featureRules,
        semantic_faces=draft.geometryRecipe.semanticFaces,
        interfaces=draft.interfaces,
    )
    rule_ok = not any(
        item.severity == "error" and not item.code.startswith("INTERFACE_")
        for item in evaluation.diagnostics
    )
    locator_ok, locator_message = _validate_semantic_face_locators(draft)
    locator_cases_ok, locator_cases_message = _validate_semantic_face_locator_cases(draft, sketch_solution)
    checks = [
        StageCheck(id="feature-review", label="制造特征规则已确认", passed=draft.featureRulesReviewed, severity="error", path="featureRulesReviewed", message="即使零件没有制造特征，也需确认规则集合为空。"),
        StageCheck(id="feature-count", label="制造特征规则已建立", passed=len(draft.featureRules) > 0, severity="warning", path="featureRules", message="当前为无制造特征零件。"),
        StageCheck(id="feature-rules", label="制造特征规则可求值", passed=rule_ok, severity="error", path="featureRules", message="特征数量、条件或坐标表达式无法安全求值。"),
        StageCheck(id="semantic-face-locators", label="语义面来源定位器引用有效", passed=locator_ok, severity="error", path="geometryRecipe.semanticFaces", message=locator_message),
        StageCheck(id="semantic-face-locator-cases", label="语义面来源三工况可解析", passed=locator_cases_ok, severity="error", path="geometryRecipe.semanticFaces", message=locator_cases_message),
        StageCheck(id="feature-expressions-id-only", label="规则表达式仅使用参数ID", passed=not feature_alias_violations, severity="error", path="featureRules", message=_alias_violation_message(feature_alias_violations) or "规则表达式仅可使用参数稳定 ID。"),
        StageCheck(id="resolved-rule-set", label="规则解析结果已检查", passed=not draft.featureRules or len(evaluation.features) > 0, severity="warning", path="featureRules", message="当前标称参数下，特征规则生成了空集合。"),
    ]
    return _validation("features", checks)


def validate_variants(
    draft: TemplateDraft,
    sketch_solution: dict[str, Any] | None = None,
) -> StageValidation:
    ids = [item.id for item in draft.parameterDefinitions]
    parameter_ids = set(ids)
    variant_ids = [item.id for item in draft.variants]
    override_keys = {key for item in draft.variants for key in item.overrides}
    try:
        parameter_evaluation_order(draft.parameterDefinitions)
        dependency_ok = True
    except RuleEvaluationError:
        dependency_ok = False
    sources_complete = all(item.sourceDefinition is not None for item in draft.parameterDefinitions)
    contract_ready = all(
        not item.declaredInRuleStage or item.contractReady
        for item in draft.parameterDefinitions
    )
    parameter_source_expressions: list[tuple[str, str]] = []
    for parameter in draft.parameterDefinitions:
        source = parameter.sourceDefinition
        if source and source.type == "formula" and source.expression:
            parameter_source_expressions.append((f"parameterDefinitions.{parameter.id}.sourceDefinition.expression", source.expression))
        if source and source.type == "lookup" and source.reference:
            parameter_source_expressions.append((f"parameterDefinitions.{parameter.id}.sourceDefinition.reference", source.reference))
    parameter_alias_violations, _ = _expression_alias_violations(parameter_source_expressions, draft)
    geometry_references, geometry_expressions_valid = _geometry_parameter_references(draft)
    geometry_contract_ok = geometry_expressions_valid and geometry_references <= parameter_ids
    semantic_face_ids = {item.id for item in draft.geometryRecipe.semanticFaces}
    feature_rule_ids = {item.id for item in draft.featureRules}
    invalid_variant_overrides: list[str] = []
    for variant in draft.variants:
        if variant.expected != "valid":
            continue
        evaluation = evaluate_template(draft.parameterDefinitions, [], overrides=variant.overrides)
        if any(item.severity == "error" for item in evaluation.diagnostics):
            invalid_variant_overrides.append(variant.id)
    interface_parameter_refs = {
        parameter_id
        for item in draft.interfaces
        for parameter_id in item.parameterRefs
    }
    interface_expression_items: list[tuple[str, str]] = []
    interface_expressions_valid = True
    for item in draft.interfaces:
        region = item.region
        if region is None or region.mode != "rectangle":
            continue
        expressions = {
            "uStartExpression": region.uStartExpression,
            "vStartExpression": region.vStartExpression,
            "uSpanExpression": region.uSpanExpression,
            "vSpanExpression": region.vSpanExpression,
            "countExpression": region.countExpression,
            "placement.pitchExpression": region.placement.pitchExpression,
            "placement.startMarginExpression": region.placement.startMarginExpression,
            "placement.endMarginExpression": region.placement.endMarginExpression,
            "placement.maximumPitchExpression": region.placement.maximumPitchExpression,
        }
        for name, expression in expressions.items():
            interface_expression_items.append((f"interfaces.{item.id}.region.{name}", expression))
            try:
                interface_parameter_refs |= expression_names(expression) - {region.indexVariable, "count"}
            except RuleEvaluationError:
                interface_expressions_valid = False
    interface_alias_violations, _ = _expression_alias_violations(interface_expression_items, draft)
    interface_geometry_refs = {
        geometry_id
        for item in draft.interfaces
        for geometry_id in [
            *item.geometryRefs,
            *( [item.referenceFrame.originRef] if item.referenceFrame.originRef else [] ),
        ]
    }
    interface_parameters_ok = interface_expressions_valid and interface_parameter_refs <= parameter_ids
    interface_geometry_ok = interface_geometry_refs <= semantic_face_ids
    interface_parameter_ids = interface_parameter_refs & parameter_ids
    interface_rule_sources_ok = all(
        item.declarationMode != "featureDerived"
        or bool(item.sourceFeatureRuleId and item.sourceFeatureRuleId in feature_rule_ids)
        for item in draft.interfaces
    )
    # Contract checks must catch incomplete declarations and contradictory
    # dimensions before the user advances to CAD review.  Keep these checks
    # here (rather than in the UI) so GUI and API/MCP callers see the same
    # actionable reasons.
    parameter_completeness_reasons: list[str] = []
    for parameter in draft.parameterDefinitions:
        if parameter.id not in interface_parameter_ids:
            continue
        if not parameter.id.strip() or not parameter.label.strip():
            parameter_completeness_reasons.append(f"参数 {parameter.id or '<未命名>'} 缺少名称")
        if parameter.default is None:
            parameter_completeness_reasons.append(f"参数 {parameter.id} 未填写默认值")
        if parameter.exposed and parameter.sourceDefinition is None:
            parameter_completeness_reasons.append(f"公开参数 {parameter.id} 未声明来源")
        source = parameter.sourceDefinition
        if source and source.type == "formula" and not (source.expression or "").strip():
            parameter_completeness_reasons.append(f"公式参数 {parameter.id} 未填写派生公式")
        if source and source.type == "lookup" and not (source.reference or "").strip():
            parameter_completeness_reasons.append(f"查表参数 {parameter.id} 未填写查表键表达式")
        if source and source.type in {
            "materialProperty", "productConfig", "componentConfig", "projectZone",
            "standard", "geometricMeasurement", "externalApi",
        } and not (source.reference or "").strip() and source.fallback is None:
            parameter_completeness_reasons.append(f"参数 {parameter.id} 的 {source.type} 来源缺少数据路径或回退值")
    parameter_completeness_ok = not parameter_completeness_reasons

    parameter_values, _, parameter_diagnostics = resolve_parameters(draft.parameterDefinitions)
    contradiction_reasons: list[str] = []
    all_numeric_values = {
        item.id: float(value)
        for item in draft.parameterDefinitions
        if item.id in parameter_values
        and isinstance((value := parameter_values[item.id]), (int, float))
        and not isinstance(value, bool)
    }
    interface_numeric_values = {
        parameter_id: value
        for parameter_id, value in all_numeric_values.items()
        if parameter_id in interface_parameter_ids
    }
    for first, second, relation, message in (
        ("thickness", "sectionWidth", "<", "壁厚必须小于截面宽度"),
        ("thickness", "sectionHeight", "<=", "壁厚必须小于等于截面高度"),
        ("length", "thickness", ">", "长度必须大于壁厚"),
    ):
        if first not in interface_parameter_ids or second not in interface_parameter_ids:
            continue
        if first in interface_numeric_values and second in interface_numeric_values:
            if relation == "<":
                valid = interface_numeric_values[first] < interface_numeric_values[second]
            elif relation == "<=":
                valid = interface_numeric_values[first] <= interface_numeric_values[second]
            else:
                valid = interface_numeric_values[first] > interface_numeric_values[second]
            if not valid:
                contradiction_reasons.append(f"{first}={interface_numeric_values[first]:g} 与 {second}={interface_numeric_values[second]:g} 矛盾：{message}")
    contradiction_reasons.extend(
        item.message
        for item in parameter_diagnostics
        if item.severity == "error"
        and item.path.startswith("parameterDefinitions.")
        and item.path.split(".", 2)[1] in interface_parameter_ids
    )
    parameter_consistency_ok = not contradiction_reasons

    interface_reasons: list[str] = []
    interface_ids: set[str] = set()
    for item in draft.interfaces:
        if item.id in interface_ids:
            interface_reasons.append(f"接口 ID 重复：{item.id}")
        interface_ids.add(item.id)
        if not item.name.strip():
            interface_reasons.append(f"接口 {item.id} 未填写名称")
        if item.declarationMode == "staticGeometry" and not item.geometryRefs:
            interface_reasons.append(f"静态接口 {item.id} 未关联语义面")
        if item.declarationMode == "staticGeometry" and len(item.geometryRefs) > 1:
            interface_reasons.append(f"静态接口 {item.id} 只能关联一个语义面")
        if item.declarationMode == "featureDerived" and not item.sourceFeatureRuleId:
            interface_reasons.append(f"特征派生接口 {item.id} 未选择来源制造特征规则")
        if item.required and not item.reviewed:
            interface_reasons.append(f"关键接口 {item.id} 尚未完成工程师复核")
    interfaces_complete = not interface_reasons

    evaluation = evaluate_template(
        draft.parameterDefinitions,
        draft.featureRules,
        semantic_faces=draft.geometryRecipe.semanticFaces,
        interfaces=draft.interfaces,
    )
    resolved_interface_sources = {
        item.sourceInterfaceId for item in evaluation.resolvedInterfaces
    }
    for item in draft.interfaces:
        if item.declarationMode == "featureDerived" and item.sourceFeatureRuleId in feature_rule_ids and item.id not in resolved_interface_sources:
            interface_reasons.append(f"特征派生接口 {item.id} 在标称参数下未生成任何实例")
    interfaces_complete = not interface_reasons
    sketch_solution = sketch_solution or solve_semantic_sketch(draft, all_numeric_values)
    feasibility_reasons = [
        item.message for item in evaluation.diagnostics if item.severity == "error"
    ]
    feasibility_reasons.extend(
        item["message"] for item in sketch_solution["diagnostics"] if item["severity"] == "error"
    )
    feasibility_reasons.extend(contradiction_reasons)
    feasibility_reasons.extend(interface_reasons)
    part_feasible = not feasibility_reasons and sketch_solution["valid"]
    checks = [
        StageCheck(id="geometry-parameter-contract", label="几何参数契约完整", passed=geometry_contract_ok, severity="error", path="parameterDefinitions", message=f"几何配方与语义面只能引用已声明参数：{', '.join(sorted(geometry_references - parameter_ids)) or '请检查表达式语法'}。"),
        StageCheck(id="parameter-ids", label="参数标识唯一", passed=len(ids) == len(set(ids)), severity="error", path="parameterDefinitions", message="参数标识不能重复。"),
        StageCheck(id="nominal-variant", label="标称实例已定义", passed="nominal" in variant_ids, severity="error", path="variants", message="必须保留 nominal 标称实例。"),
        StageCheck(id="variant-ids", label="变体标识唯一", passed=len(variant_ids) == len(set(variant_ids)), severity="error", path="variants", message="变体标识不能重复。"),
        StageCheck(id="override-keys", label="变体覆盖参数有效", passed=override_keys <= parameter_ids, severity="error", path="variants", message="变体只能覆盖已声明参数。"),
        StageCheck(id="variant-overrides-evaluable", label="有效变体参数可求值", passed=not invalid_variant_overrides, severity="error", path="variants", message=f"以下有效变体的参数覆盖无法通过类型、范围或依赖检查：{', '.join(invalid_variant_overrides) or '无'}。"),
        StageCheck(id="parameter-source-expressions-id-only", label="参数来源表达式仅使用参数ID", passed=not parameter_alias_violations, severity="error", path="parameterDefinitions", message=_alias_violation_message(parameter_alias_violations) or "参数来源表达式仅可使用参数稳定 ID。"),
        StageCheck(id="interface-parameter-refs", label="接口参数引用有效", passed=interface_parameters_ok, severity="error", path="interfaces", message=(f"接口区域表达式语法无效；" if not interface_expressions_valid else "") + f"接口只能引用已声明参数：{', '.join(sorted(interface_parameter_refs - parameter_ids)) or '无缺失参数'}。"),
        StageCheck(id="interface-expressions-id-only", label="接口区域表达式仅使用参数ID", passed=not interface_alias_violations, severity="error", path="interfaces", message=_alias_violation_message(interface_alias_violations) or "接口区域表达式仅可使用参数稳定 ID。"),
        StageCheck(id="interface-geometry-refs", label="接口几何引用有效", passed=interface_geometry_ok, severity="error", path="interfaces", message=f"接口只能引用已声明语义面：{', '.join(sorted(interface_geometry_refs - semantic_face_ids)) or '无缺失语义面'}。"),
        StageCheck(id="interface-rule-sources", label="特征派生接口来源有效", passed=interface_rule_sources_ok, severity="error", path="interfaces", message="特征派生接口必须选择已有制造特征规则。"),
        StageCheck(id="interface-completeness", label="零部件接口定义完整且合理", passed=interfaces_complete, severity="error", path="interfaces", message="；".join(interface_reasons) or "接口声明完整，关联基准和复核状态有效。"),
        StageCheck(id="parameter-sources", label="参数来源完整", passed=sources_complete, severity="error", path="parameterDefinitions", message="每个参数必须声明用户输入、材料属性、公式、查表或外部配置来源。"),
        StageCheck(id="parameter-completeness", label="接口引用参数输入完整", passed=parameter_completeness_ok, severity="error", path="parameterDefinitions", message="；".join(parameter_completeness_reasons) or "接口引用参数均已填写默认值并声明来源。"),
        StageCheck(id="parameter-consistency", label="接口参数之间无矛盾", passed=parameter_consistency_ok, severity="error", path="parameterDefinitions", message="；".join(contradiction_reasons) or "接口引用参数的范围、依赖和关键尺寸关系一致。"),
        StageCheck(id="parameter-contract-ready", label="规则预声明参数已补全契约", passed=contract_ready, severity="error", path="parameterDefinitions", message="规则页预声明的参数需要在契约页补全后，才能进入试算、验证与发布。"),
        StageCheck(id="parameter-dependency", label="参数依赖图有效", passed=dependency_ok, severity="error", path="parameterDefinitions", message="参数存在未知依赖或循环依赖。"),
        StageCheck(id="part-feasibility", label="零部件生成可行", passed=part_feasible, severity="error", path="geometryRecipe", message="；".join(feasibility_reasons) or "标称参数下草图、特征和接口均可解析，零部件具备生成条件。"),
    ]
    return _validation("variants", checks)


def validate_review(draft: TemplateDraft, compile_result: CompileResult | None, expected_hash: str | None) -> StageValidation:
    result_ok = bool(compile_result and compile_result.success)
    hash_ok = bool(result_ok and expected_hash and compile_result and compile_result.inputHash == expected_hash)
    metrics_ok = bool(result_ok and compile_result and compile_result.metrics and compile_result.metrics.valid and compile_result.metrics.solidCount == 1)
    checks = [
        StageCheck(id="compile-success", label="真实CAD编译成功", passed=result_ok, severity="error", path="compile", message="请运行 OpenCascade 编译并修复全部错误。"),
        StageCheck(id="current-input", label="审查结果对应当前输入", passed=hash_ok, severity="error", path="compile.inputHash", message="上游定义已变化，请重新编译。"),
        StageCheck(id="brep-valid", label="B-Rep 拓扑有效", passed=metrics_ok, severity="error", path="compile.metrics", message="要求有效单实体且体积为正。"),
    ]
    return _validation("review", checks)


def validate_admission(draft: TemplateDraft, review: StageValidation) -> StageValidation:
    upstream = all(getattr(draft.stageStatus, stage) == "complete" for stage in STAGE_ORDER[:-1])
    checks = [
        StageCheck(id="upstream-complete", label="前置阶段全部完成", passed=upstream, severity="error", path="stageStatus", message="模板信息至三维审查必须全部完成。"),
        StageCheck(id="review-current", label="当前几何通过审查", passed=review.complete, severity="error", path="review", message="当前输入必须有成功的CAD审查结果。"),
        StageCheck(id="reviewer", label="发布复核人已指定", passed=len(draft.admission.reviewer.strip()) >= 2, severity="error", path="admission.reviewer", message="请填写发布复核人。"),
        StageCheck(id="change-note", label="版本说明完整", passed=len(draft.admission.changeNote.strip()) >= 5, severity="error", path="admission.changeNote", message="请填写本版本的变更与适用范围。"),
    ]
    return _validation("admission", checks)


def validate_stage(
    stage: StageName,
    draft: TemplateDraft,
    *,
    code_unique: bool = True,
    material_samples: list[dict[str, Any]] | None = None,
    compile_result: CompileResult | None = None,
    expected_hash: str | None = None,
    sketch_solution: dict[str, Any] | None = None,
) -> StageValidation:
    if stage == "templateInfo":
        return validate_template_info(draft, code_unique=code_unique)
    if stage == "material":
        return validate_material(draft, material_samples)
    if stage == "baseSketch":
        return validate_base_sketch(draft, sketch_solution)
    if stage == "features":
        return validate_features(draft, sketch_solution)
    if stage == "variants":
        return validate_variants(draft, sketch_solution)
    review = validate_review(draft, compile_result, expected_hash)
    return review if stage == "review" else validate_admission(draft, review)


def stage_fingerprint(stage: StageName, draft: TemplateDraft) -> tuple:
    if stage == "templateInfo":
        return template_info_fingerprint(draft)
    if stage == "material":
        return (tuple(item.model_dump_json() for item in draft.materialRequirements), tuple(item.model_dump_json() for item in draft.materialValidationSamples), draft.blank.model_dump_json())
    if stage == "baseSketch":
        return (draft.geometryPrototypeId, draft.sketch.model_dump_json(), draft.geometryRecipe.model_dump_json(), tuple(item.model_dump_json() for item in draft.parameterDefinitions))
    if stage == "features":
        return (draft.featureRulesReviewed, tuple(item.model_dump_json() for item in draft.featureRules))
    if stage == "variants":
        return tuple(item.model_dump_json() for item in draft.parameterDefinitions + draft.variants + draft.interfaces)
    if stage == "admission":
        return (draft.admission.model_dump_json(),)
    return ()
