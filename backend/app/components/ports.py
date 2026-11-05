"""External contracts derive their component frame from the pinned source member."""
import hashlib
import json
import math


def resolve_port(document, port):
    from .port_frames import decompose, multiply, transform

    member = next((item for item in document.assembly.members if item.id == port.source.memberId), None)
    source = next((item for item in member.interfaces if item.id == port.source.interfaceId), None) if member else None
    issues = []
    if not member or not source:
        issues.append({"code": "SOURCE_MISSING", "message": "来源成员或接口已失效，请重新选择来源"})
    elif not source.frame:
        issues.append({"code": "FRAME_REQUIRED", "message": "来源接口未校准，需补充来源接口坐标后复核"})
    if sum(item.source == port.source for item in document.ports) > 1:
        issues.append({"code": "DUPLICATE_SOURCE", "message": "同一来源接口被重复暴露"})
    if not port.id.strip() or sum(item.id == port.id for item in document.ports) > 1:
        issues.append({"code": "INVALID_ID", "message": "接口 ID 为空或重复"})
    if not port.name.strip():
        issues.append({"code": "NAME_REQUIRED", "message": "请填写对外接口名称"})
    if not any(tag.strip() for tag in port.compatibleTags or []):
        issues.append({"code": "COMPATIBILITY_REQUIRED", "message": "尚未定义可连接对象的标签"})
    if not port.allowedRelations:
        issues.append({"code": "RELATIONS_REQUIRED", "message": "尚未选择允许的连接方式"})
    frame = None
    if member and source and source.frame:
        origin, rotation = decompose(multiply(transform(member.position, member.rotation), transform(source.frame.origin, source.frame.rotation)))
        frame = {"origin": origin, "rotation": rotation}
    return {"portId": port.id, "status": "needs-review" if issues else "ready", "type": source.type if source else None,
            "frame": frame, "sourceVersion": member.template.model_dump() if member else None, "issues": issues}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source_context(document, port):
    member = next((item for item in document.assembly.members if item.id == port.source.memberId), None)
    source = next((item for item in member.interfaces if item.id == port.source.interfaceId), None) if member else None
    return {"source": port.source.model_dump(), "template": member.template.model_dump() if member else None,
            "parameterValues": member.parameterValues if member else None, "position": member.position if member else None,
            "rotation": member.rotation if member else None, "interface": source.model_dump() if source else None}


def trial_context(document, port):
    return {"assembly": document.assembly.model_dump(by_alias=True),
            "contract": port.model_dump(exclude={"trial", "reviewConfirmation"}),
            "unitSystem": document.unitSystem, "coordinateSystem": document.coordinateSystem}


def specification_issues(port):
    spec = port.specification
    if spec is None:
        return ["未填写结构化接口规格"]
    issues = []
    if not spec.category.strip(): issues.append("接口类别未确定")
    if spec.role == "unknown": issues.append("配合角色未确定")
    if spec.contact == "unknown": issues.append("接触方式未确定")
    if spec.clearanceMinMm is None or spec.clearanceMaxMm is None: issues.append("允许间隙范围未确定")
    if not spec.dimensions: issues.append("缺少关键尺寸")
    if any(item.value is None or item.tolerance is None for item in spec.dimensions): issues.append("关键尺寸或公差未确定")
    return issues


def basis_current(document, port):
    return bool(port.basisConfirmation and resolve_port(document, port)["frame"] and port.basisConfirmation.key == digest(source_context(document, port)))


def port_diagnostics(document, targets=None):
    results = []
    for port in document.ports:
        result = resolve_port(document, port)
        basis = basis_current(document, port)
        reasons = []
        source_changes = []
        if port.basisConfirmation and not basis:
            labels = {"source": "来源引用", "template": "发布版本/校验值", "parameterValues": "成员参数", "position": "成员位置", "rotation": "成员方向", "interface": "接口基准/类型"}
            old = port.basisConfirmation.snapshot
            current = source_context(document, port)
            source_changes = [label for key, label in labels.items() if old.get(key) != current.get(key)]
            reasons.append("基准确认已过期：" + "、".join(source_changes or ["来源数据变化"]))
        current_trial = bool(port.trial and port.trial.sourceKey == digest(trial_context(document, port)))
        if port.trial:
            if not current_trial: reasons.append("本组件的成员、参数、基准或连接规则变化，试配结果需重跑")
            target = (targets or {}).get(port.trial.options.targetDraftId)
            target_port = next((item for item in target.ports if item.id == port.trial.options.targetPortId), None) if target else None
            if targets is None:
                current_trial = False
                reasons.append("尚未核对目标草稿的当前版本")
            elif not target_port:
                current_trial = False
                reasons.append("目标草稿或对外接口已失效")
            elif target.revision != port.trial.options.expectedTargetRevision or digest(trial_context(target, target_port)) != port.trial.targetKey:
                current_trial = False
                reasons.append("目标草稿版本或接口定义变化，试配结果需重跑")
        frame = result["frame"]
        axes = None
        if frame:
            from .port_frames import transform
            matrix = transform(frame["origin"], frame["rotation"])
            axes = {name: [matrix[row][column] for row in range(3)] for column, name in enumerate(("x", "y", "z"))}
        review = bool(port.reviewConfirmation and current_trial and basis and port.reviewConfirmation.key == digest({"context": trial_context(document, port), "trial": port.trial.model_dump() if port.trial else None}))
        if port.reviewConfirmation and not review: reasons.append("人工确认已过期，需在重新试配后再次确认")
        result.update(axes=axes, sourceChanges=source_changes, reviewReasons=reasons, trialCurrent=current_trial,
            layers={"definition": "pass" if result["status"] == "ready" and not specification_issues(port) else "pending",
                    "basis": "pass" if basis else "stale" if port.basisConfirmation else "pending",
                    "rules": port.trial.report["rules"]["status"] if current_trial else "stale" if port.trial else "pending",
                    "geometry": "alignment-pass-collision-pending" if current_trial and port.trial.report["alignment"]["status"] == "pass" else "stale" if port.trial and not current_trial else "pending",
                    "review": "pass" if review else "stale" if port.reviewConfirmation else "pending"})
        result["specificationIssues"] = specification_issues(port)
        results.append(result)
    return results


def match_rules(document, port, target_document, target_port, relation):
    issues, dimensions = [], []
    def issue(code, message, status="fail"):
        issues.append({"code": code, "message": message, "status": status})
    for label, doc, item in (("本接口", document, port), ("目标接口", target_document, target_port)):
        if not basis_current(doc, item): issue("BASIS_UNCONFIRMED", label + "的基准未确认或确认已过期", "pending")
        for message in specification_issues(item): issue("SPECIFICATION_INCOMPLETE", label + "：" + message, "pending")
        if not item.compatibleTags: issue("TAGS_UNDEFINED", label + "尚未定义对方标签要求", "pending")
        if not item.allowedRelations: issue("RELATIONS_UNDEFINED", label + "尚未定义连接方式", "pending")
        elif relation not in item.allowedRelations: issue("RELATION_MISMATCH", label + "不允许" + relation)
    if any(tag not in target_port.tags for tag in port.compatibleTags or []): issue("TARGET_TAG_MISMATCH", "目标未具备本接口要求的全部标签")
    if any(tag not in port.tags for tag in target_port.compatibleTags or []): issue("SOURCE_TAG_MISMATCH", "本接口未具备目标要求的全部标签")
    a, b = port.specification, target_port.specification
    left, right = resolve_port(document, port), resolve_port(target_document, target_port)
    if left["type"] != right["type"]: issue("INTERFACE_TYPE_MISMATCH", "接口几何类型不同")
    if a and b:
        if a.category.strip() and b.category.strip() and a.category.strip() != b.category.strip(): issue("CATEGORY_MISMATCH", "接口类别不同")
        roles_ok = (a.role, b.role) in (("male", "female"), ("female", "male"), ("symmetric", "symmetric"))
        if "unknown" not in (a.role, b.role) and not roles_ok: issue("ROLE_MISMATCH", "配合角色不互补")
        if a.contact != "unknown" and b.contact != "unknown" and a.contact != b.contact: issue("CONTACT_MISMATCH", "接触方式不一致")
        by_a, by_b = {item.key: item for item in a.dimensions}, {item.key: item for item in b.dimensions}
        factor = {"mm": 1, "cm": 10, "m": 1000}
        for key in sorted(set(by_a) | set(by_b)):
            da, db = by_a.get(key), by_b.get(key)
            av = da.value * factor[da.unit] if da and da.value is not None else None
            bv = db.value * factor[db.unit] if db and db.value is not None else None
            row = {"key": key, "movingMm": av, "targetMm": bv, "status": "pending"}
            if av is None or bv is None or not da or not db:
                issue("DIMENSION_MISSING", key + "缺少一侧尺寸值", "pending")
            elif da.tolerance is None or db.tolerance is None:
                issue("TOLERANCE_UNKNOWN", key + "公差未确定", "pending")
            elif roles_ok:
                gap = (bv-av if a.role == "male" else av-bv) if a.role != "symmetric" else abs(av-bv)
                uncertainty = da.tolerance * factor[da.unit] + db.tolerance * factor[db.unit]
                row.update(clearanceMm=gap, uncertaintyMm=uncertainty)
                bounds = (a.clearanceMinMm, a.clearanceMaxMm, b.clearanceMinMm, b.clearanceMaxMm)
                if all(value is not None for value in bounds):
                    low, high = max(bounds[0], bounds[2]), min(bounds[1], bounds[3])
                    # Symmetric interfaces compare absolute mismatch; a zero mismatch has no negative clearance.
                    lower = max(0, gap-uncertainty) if a.role == "symmetric" else gap-uncertainty
                    passed = lower >= low-1e-9 and gap+uncertainty <= high+1e-9
                    row["status"] = "pass" if passed else "fail"
                    if not passed: issue("CLEARANCE_OUT_OF_RANGE", f"{key}：间隙/尺寸差 {gap:.4g} ± {uncertainty:.4g} mm，不满足双方共同范围 [{low:.4g}, {high:.4g}] mm")
            dimensions.append(row)
    return {"status": "fail" if any(item["status"] == "fail" for item in issues) else "pending" if issues else "pass", "issues": issues, "dimensions": dimensions}


def trial_ports(document, port, target_document, target_port, options):
    from .port_frames import decompose, inverse, multiply, transform
    rules = match_rules(document, port, target_document, target_port, options.relation)
    a, b = resolve_port(document, port)["frame"], resolve_port(target_document, target_port)["frame"]
    moving = [item.model_dump() for item in document.assembly.members]
    alignment = {"status": "pending"}
    if a and b:
        source = transform(a["origin"], a["rotation"])
        target = transform(b["origin"], b["rotation"])
        desired = multiply(multiply(target, transform([0, 0, options.offset], [0, 0, options.angle])), transform([0, 0, 0], [math.pi if options.flip else 0, 0, 0]))
        delta = multiply(desired, inverse(source))
        def error(matrix):
            return math.sqrt(sum(matrix[i][3]**2 for i in range(3))), math.acos(max(-1, min(1, (sum(matrix[i][i] for i in range(3))-1)/2)))
        before_distance, before_angle = error(multiply(inverse(desired), source))
        distance, angle = error(multiply(inverse(desired), multiply(delta, source)))
        for member in moving:
            member["position"], member["rotation"] = decompose(multiply(delta, transform(member["position"], member["rotation"])))
        alignment = {"status": "pass" if distance < 1e-5 and angle < 1e-6 else "fail", "positionErrorMm": distance, "angleErrorRad": angle, "beforeDistanceMm": before_distance, "beforeAngleRad": before_angle}
    collision = {"status": "pending", "method": "not-run", "message": "本次检查接口刚体对齐和规格规则，尚未执行网格或实体碰撞；间隙来自声明的接口尺寸。"}
    report = {"status": "fail" if rules["status"] == "fail" or alignment["status"] == "fail" else "pending", "rules": rules, "alignment": alignment, "collision": collision}
    return {"report": report, "movingMembers": moving, "targetMembers": [item.model_dump() for item in target_document.assembly.members]}
