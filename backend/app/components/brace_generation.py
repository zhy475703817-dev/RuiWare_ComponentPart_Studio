"""Expand declarative column-brace rules into fixed-size assembly instances."""
from __future__ import annotations

from copy import deepcopy
import math

from .geometry_common import ResizeFailure, fingerprint
from .brace_policy import INDEX_TOKEN, active_brace_rules, brace_relation_owner, brace_rule_conflicts, valid_interface_pattern


def _interface_id(pattern: str, index: int) -> str:
    return INDEX_TOKEN.sub(lambda match: f"{index:0{int(match.group(1) or 0)}d}", pattern)


def _parameter(document: dict, identifier: str) -> float:
    item = next((p for p in document.get("parameters", []) if p.get("id") == identifier), None)
    value = item.get("value") if item else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ResizeFailure(f"撑杆规则参数无效：{identifier}", "BRACE_RULE_PARAMETER_INVALID")
    if item.get('unit') != 'mm':
        raise ResizeFailure(f"撑杆规则参数 {identifier} 必须使用 mm 单位", 'BRACE_RULE_UNIT_INVALID')
    minimum, maximum = item.get('min'), item.get('max')
    if value < -1e-6 or minimum is not None and value < minimum - 1e-6 or maximum is not None and value > maximum + 1e-6:
        message = f"撑杆规则参数 {identifier} 的值 {value:g} 超出允许范围"
        raise ResizeFailure(message, 'BRACE_RULE_PARAMETER_RANGE', [{
            'parameterId': identifier, 'minimum': minimum, 'maximum': maximum, 'value': value, 'message': message,
        }])
    return float(value)


def _index_value(value, label: str, default: int | None = None) -> int:
    if value is None and default is not None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10000:
        raise ResizeFailure(f"{label}无效：必须是0到10000之间的整数", "BRACE_RULE_RANGE_INVALID")
    return value


def _copy_member(source: dict, rule_id: str, index: int, prefix: str, pitch: float) -> dict:
    member = deepcopy(source)
    member["id"] = f"{prefix}-{rule_id}-{source['id']}-{index}"
    member["generatedBy"] = rule_id
    member["generatedIndex"] = index
    member.setdefault("position", [0, 0, 0])
    member["position"] = list(member["position"])
    member["position"][2] += pitch * index
    return member


def materialize_brace_generation_interfaces(document: dict, provider) -> dict:
    """Merge provider-resolved source interfaces before brace expansion.

    Feature arrays such as column hole rows are often absent from a draft's
    static interface list and only become concrete after CAD evaluation. The
    brace rule still needs those identities while it creates mates, so expose
    the provider result on the source members first. Existing metadata (for
    example hardware assignments) is retained when an identity is repeated.
    """
    rules = active_brace_rules(document)
    if not rules:
        return {}
    _check_rule_sources(document)
    geometries = {}
    members = {member.get("id"): member for member in document.get("assembly", {}).get("members", [])}
    parameters = {item.get("id"): item.get("value") for item in document.get("parameters", [])}
    bindings = document.get("bindings", [])
    source_ids = {
        member_id
        for rule in rules
        for member_id in (rule.get("leftColumnMemberId"), rule.get("rightColumnMemberId"))
        if member_id
    }
    for member_id in source_ids:
        member = members.get(member_id)
        if member is None:
            continue
        # Resize requests update the component-level height first. Apply the
        # same binding that normal assembly evaluation uses before asking CAD
        # for dynamic feature occurrences, otherwise a taller target still
        # gets the old column length and old hole count.
        evaluated_parameters = dict(member.get("parameterValues", {}))
        for binding in bindings:
            if binding.get("memberId") == member_id and binding.get("parameterId") in parameters:
                value = parameters[binding["parameterId"]]
                if value is not None:
                    evaluated_parameters[binding["memberParameterId"]] = value
        # Keep the resize-local member state aligned with the values used for
        # the provider evaluation.  Otherwise materialized hole identities can
        # describe the target height while the solver's CAD baseline still
        # evaluates the old member length.
        member["parameterValues"] = evaluated_parameters
        try:
            geometry = provider.evaluate(member["template"], evaluated_parameters)
        except Exception as error:
            raise ResizeFailure(
                f"柱片 {member.get('name', member_id)} 的真实孔位无法解析：{error}",
                "BRACE_RULE_INTERFACE_MISSING",
                [{"memberId": member_id, "message": str(error)}],
            ) from error
        geometries[member_id] = geometry
        existing = {item.get("id"): item for item in member.get("interfaces", []) if item.get("id")}
        previous_generated_ids = {
            item.get("id") for item in member.get("resolvedInterfaces", [])
            if item.get("id") and item.get("declarationMode") == "featureDerived"
            and item.get("sourceFeatureRuleId") and item.get("sourceFeatureId")
        }
        merged = []
        for item in geometry.interfaces:
            identifier = item.get("id")
            if not identifier:
                continue
            merged.append({**deepcopy(existing.get(identifier, {})), **deepcopy(item)})
        for item in member.get("interfaces", []):
            if (item.get("id") and item["id"] not in previous_generated_ids
                    and item["id"] not in {entry["id"] for entry in merged}):
                merged.append(deepcopy(item))
        member["interfaces"] = merged
        if getattr(geometry, "resolved", None):
            member["resolvedInterfaces"] = deepcopy(geometry.resolved)
    for rule in rules:
        for member_id in (rule.get("horizontalMemberId"), rule.get("diagonalMemberId")):
            if member_id in members and member_id not in geometries:
                member = members[member_id]
                geometries[member_id] = provider.evaluate(member["template"], member.get("parameterValues", {}))
    return geometries


def _check_rule_sources(document):
    if conflicts := brace_rule_conflicts(document):
        raise ResizeFailure(conflicts[0]['message'], conflicts[0]['code'], conflicts)
    for rule in active_brace_rules(document):
        for kind in ('horizontal', 'diagonal'):
            left, right = rule.get(kind + 'LeftInterfaceId'), rule.get(kind + 'RightInterfaceId')
            if left and left == right:
                raise ResizeFailure(f"规则 {rule.get('id', '')} 的撑杆两端不能引用同一个接口", 'BRACE_RULE_INTERFACE_INVALID')
        for key in ('heightParameterId', 'pitchParameterId', 'bottomOffsetParameterId',
                    'topClearanceParameterId', 'diagonalPitchParameterId'):
            if rule.get(key):
                _parameter(document, rule[key])
        for key in ('leftColumnInterfacePattern', 'rightColumnInterfacePattern',
                    'diagonalLeftColumnInterfacePattern', 'diagonalRightColumnInterfacePattern'):
            pattern = rule.get(key)
            if pattern is None:
                continue
            if not valid_interface_pattern(pattern):
                message = f"规则 {rule.get('id', '')} 的 {key} 必须含一个编号占位符，如 hole.{{index:03d}}；补零宽度限1到10"
                raise ResizeFailure(message, 'BRACE_RULE_PATTERN_INVALID', [{'ruleId': rule.get('id'), 'field': key, 'message': message}])


def _place_brace(brace, prototype, left_column, right_column, left_port, right_port,
                 left_column_port, right_column_port, geometries):
    """Seed a rigid port-to-port placement, including alternating diagonals."""
    if not geometries:
        return
    import numpy as np
    from scipy.spatial.transform import Rotation
    from .resize_geometry import matrix

    shape = geometries[prototype['id']]
    local_a, local_b = shape.frames.get(left_port), shape.frames.get(right_port)
    column_a = geometries[left_column['id']].frames.get(left_column_port)
    column_b = geometries[right_column['id']].frames.get(right_column_port)
    if any(frame is None for frame in (local_a, local_b, column_a, column_b)):
        # Incomplete intermediate drafts can still be inspected. The solver
        # rejects any connected endpoint without CAD frames before solving.
        return
    transform_a = matrix(left_column['position'], left_column['rotation'])
    transform_b = matrix(right_column['position'], right_column['rotation'])
    target_a = transform_a[:3, :3] @ column_a['center'] + transform_a[:3, 3]
    target_b = transform_b[:3, :3] @ column_b['center'] + transform_b[:3, 3]
    local_vector = local_b['center'] - local_a['center']
    target_vector = target_b - target_a
    if np.linalg.norm(local_vector) < 1e-9 or np.linalg.norm(target_vector) < 1e-9:
        raise ResizeFailure("撑杆两端节点必须是不同的真实孔位", "BRACE_RULE_INTERFACE_MISSING")
    # Port vectors determine the rigid rod direction; the real face normal
    # removes the remaining twist ambiguity without reflecting the geometry.
    local_u = local_vector / np.linalg.norm(local_vector)
    local_n = local_a['normal'] - local_u * np.dot(local_a['normal'], local_u)
    target_u = target_vector / np.linalg.norm(target_vector)
    target_n = -(transform_a[:3, :3] @ column_a['normal'])
    target_n -= target_u * np.dot(target_n, target_u)
    if min(np.linalg.norm(local_n), np.linalg.norm(target_n)) < 1e-9:
        # Axis ports have no transverse normal; retain the source twist while
        # rotating its port vector onto the desired connection direction.
        rotation = matrix(prototype['position'], prototype['rotation'])[:3, :3]
        original_u = rotation @ local_u
        axis = np.cross(original_u, target_u)
        angle = np.arctan2(np.linalg.norm(axis), np.clip(np.dot(original_u, target_u), -1, 1))
        if np.linalg.norm(axis) < 1e-9:
            if np.dot(original_u, target_u) < 0:
                axis = rotation[:, int(np.argmin(np.abs(rotation.T @ original_u)))]; angle = np.pi
            else:
                angle = 0
        if angle:
            rotation = Rotation.from_rotvec(axis / np.linalg.norm(axis) * angle).as_matrix() @ rotation
    else:
        local_n /= np.linalg.norm(local_n); target_n /= np.linalg.norm(target_n)
        local_basis = np.column_stack((local_u, local_n, np.cross(local_u, local_n)))
        target_basis = np.column_stack((target_u, target_n, np.cross(target_u, target_n)))
        rotation = target_basis @ local_basis.T
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        brace['rotation'] = Rotation.from_matrix(rotation).as_euler('XYZ').tolist()
    brace['position'] = (target_a - rotation @ local_a['center']).tolist()


def _mate(rule_id: str, index: int, kind: str, side: str, brace_id: str, brace_interface: str,
          column_id: str, column_interface: str, relation: str) -> dict:
    return {
        "id": f"__brace__{rule_id}__{kind}__{index}__{side}",
        "from": {"memberId": brace_id, "interfaceId": brace_interface},
        "to": {"memberId": column_id, "interfaceId": column_interface},
        "relation": relation,
        "offset": 0,
    }


def _diagonal_start_offset(column, horizontal_pattern, diagonal_pattern, horizontal_start,
                           diagonal_start, pitch, geometries):
    """Measure separate hole-row origins instead of comparing their numbering."""
    if geometries:
        frames = geometries[column['id']].frames
        horizontal = frames.get(_interface_id(horizontal_pattern, horizontal_start))
        diagonal = frames.get(_interface_id(diagonal_pattern, diagonal_start))
        if horizontal is None or diagonal is None:
            raise ResizeFailure("无法读取撑杆起点的真实孔位", "BRACE_RULE_INTERFACE_MISSING")
        offset = float(diagonal['center'][2] - horizontal['center'][2])
    elif horizontal_pattern == diagonal_pattern:
        offset = (diagonal_start - horizontal_start) * pitch
    else:
        raise ResizeFailure("不同孔列的起点必须依据真实CAD孔位确定", "BRACE_RULE_INTERFACE_MISSING")
    if offset < -1e-6:
        raise ResizeFailure("斜撑起点低于规则的底部孔位，请调整起点或底部余量", "BRACE_RULE_RANGE_INVALID")
    return max(0., offset)


def expand_brace_generation_rules(document: dict, geometries=None) -> list[dict]:
    """Mutate a resize-local document with instances required by each brace rule.

    Generated members and mates are removed before every expansion, so an applied
    resize can safely be resized again without accumulating duplicate instances.
    """
    rules = active_brace_rules(document)
    if not rules:
        return []
    _check_rule_sources(document)
    geometric_relations = []
    assembly = document.setdefault("assembly", {"members": [], "mates": []})
    rule_ids = [rule.get("id", "") for rule in rules]
    if len(set(rule_ids)) != len(rule_ids) or any(not rule_id for rule_id in rule_ids):
        raise ResizeFailure("撑杆规则标识重复或为空", "BRACE_RULE_INVALID")
    rule_id_set = set(rule_ids)
    assembly["members"] = [m for m in assembly["members"] if m.get("generatedBy") not in rule_id_set]
    assembly["mates"] = [
        m for m in assembly["mates"]
        if brace_relation_owner(m.get('id')) not in rule_id_set
    ]

    members = {m["id"]: m for m in assembly["members"]}
    used_ids = set(members)
    occupied_spans = {}
    for rule in rules:
        rule_id = rule.get("id", "")
        required = (
            "leftColumnMemberId", "rightColumnMemberId", "horizontalMemberId", "diagonalMemberId",
            "heightParameterId", "pitchParameterId", "bottomOffsetParameterId", "topClearanceParameterId",
        )
        if any(not rule.get(key) for key in required):
            raise ResizeFailure(f"撑杆生成规则不完整：{rule_id}", "BRACE_RULE_INVALID")
        left = members.get(rule["leftColumnMemberId"])
        right = members.get(rule["rightColumnMemberId"])
        horizontal = members.get(rule["horizontalMemberId"])
        diagonal = members.get(rule["diagonalMemberId"])
        if not left or not right or not horizontal or not diagonal:
            raise ResizeFailure(f"撑杆规则引用了不存在的成员：{rule_id}", "BRACE_RULE_MEMBER_MISSING")
        def connect(connections):
            brace_id = connections[0]['from']['memberId']
            brace = members[brace_id]
            # Sharing target holes alone is not proof of duplicate solids:
            # different profiles or reversed asymmetric rods can be intentional.
            # Reject the same source geometry and same port-to-node mapping.
            shape_key = {
                'template': {**brace['template'], 'templateKind': brace['template'].get('templateKind', 'part')},
                'parameters': {key: float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
                               for key, value in brace['parameterValues'].items()},
            }
            signature = (fingerprint(shape_key),
                         tuple(sorted((item['from']['interfaceId'], item['to']['memberId'], item['to']['interfaceId'])
                                      for item in connections)))
            if signature in occupied_spans:
                previous = occupied_spans[signature]
                message = f"规则 {rule_id} 的撑杆 {brace_id} 与规则 {previous['ruleId']} 的撑杆 {previous['memberId']} 以相同规格和端口对应关系重复连接同一对孔位，请检查重复规则"
                raise ResizeFailure(message, 'BRACE_RULE_NODE_CONFLICT', [{
                    'ruleId': rule_id, 'memberId': brace_id, 'otherRuleId': previous['ruleId'],
                    'otherMemberId': previous['memberId'], 'nodes': [item['to'] for item in connections], 'message': message,
                }])
            occupied_spans[signature] = {'ruleId': rule_id, 'memberId': brace_id}
            if rule.get("connectionMode", "mates") == "mates":
                for connection in connections:
                    # An equivalent user-declared mate already supplies this
                    # constraint. Keep its identity and avoid double capacity use.
                    if not any(
                        existing.get('relation') == connection['relation'] and existing.get('offset', 0) == 0
                        and ((existing.get('from') == connection['from'] and existing.get('to') == connection['to'])
                             or (existing.get('from') == connection['to'] and existing.get('to') == connection['from']))
                        for existing in assembly['mates']
                    ):
                        assembly["mates"].append(connection)
            for connection in connections:
                mate_mode = rule.get("connectionMode", "mates") == "mates"
                if mate_mode and connection["relation"] != "贴合":
                    continue
                geometric_relations.append({
                    "id": connection["id"] + ("__node" if mate_mode else ""),
                    "a": {"path": [connection["from"]["memberId"]], "interfaceId": connection["from"]["interfaceId"]},
                    "b": {"path": [connection["to"]["memberId"]], "interfaceId": connection["to"]["interfaceId"]},
                    "mode": "axis" if mate_mode or connection["relation"] == "同轴" else "coincident",
                    "origin": "existing", "offset": [0, 0, 0], "normalDot": -1,
                })
        height = _parameter(document, rule["heightParameterId"])
        pitch = _parameter(document, rule["pitchParameterId"])
        bottom = _parameter(document, rule["bottomOffsetParameterId"])
        top = _parameter(document, rule["topClearanceParameterId"])
        if pitch <= 0 or height < bottom + top:
            raise ResizeFailure(f"撑杆规则的高度或孔距无效：{rule_id}", "BRACE_RULE_RANGE_INVALID")
        span = height - bottom - top
        index_start = _index_value(rule.get("columnInterfaceIndexStart"), "横撑孔位起点", 0)
        index_step = rule.get("columnInterfaceIndexStep", 1)
        if isinstance(index_step, bool) or not isinstance(index_step, int) or index_step < 1 or index_step > 1000:
            raise ResizeFailure(f"横撑孔位步长无效：{rule_id}", "BRACE_RULE_RANGE_INVALID")
        horizontal_pitch = pitch * index_step
        bays = math.floor(span / horizontal_pitch + 1e-9)
        remainder = span - bays * horizontal_pitch
        if rule.get("remainderPolicy", "reject") == "reject" and remainder > 1e-6:
            raise ResizeFailure(f"高度 {height:g} mm 不能按 {pitch:g} mm 孔距完整分格，请调整高度或孔距", "BRACE_RULE_REMAINDER")
        holes = bays + 1
        if holes < 2:
            raise ResizeFailure("柱片至少需要两个孔位才能生成横撑和斜撑", "BRACE_RULE_TOO_SHORT")
        diagonal_step = rule.get("diagonalColumnInterfaceIndexStep", index_step)
        if isinstance(diagonal_step, bool) or not isinstance(diagonal_step, int) or diagonal_step < 1 or diagonal_step > 1000:
            raise ResizeFailure(f"斜撑孔位步长无效：{rule_id}", "BRACE_RULE_RANGE_INVALID")
        diagonal_start = rule.get("diagonalColumnInterfaceIndexStart")
        if diagonal_start is None:
            diagonal_start = index_start
        diagonal_start = _index_value(diagonal_start, "斜撑孔位起点")
        diagonal_pitch_id = rule.get("diagonalPitchParameterId")
        diagonal_pitch = _parameter(document, diagonal_pitch_id) if diagonal_pitch_id else pitch
        if diagonal_pitch <= 0:
            raise ResizeFailure(f"斜撑节点间距无效：{rule_id}", "BRACE_RULE_RANGE_INVALID")
        left_pattern = rule.get("leftColumnInterfacePattern", "hole.{index}")
        right_pattern = rule.get("rightColumnInterfacePattern", "hole.{index}")
        diagonal_left_pattern = rule.get("diagonalLeftColumnInterfacePattern") or left_pattern
        diagonal_right_pattern = rule.get("diagonalRightColumnInterfacePattern") or right_pattern
        diagonal_offset = max(
            _diagonal_start_offset(column, horizontal_pattern, diagonal_pattern,
                                   index_start, diagonal_start, pitch, geometries)
            for column, horizontal_pattern, diagonal_pattern in (
                (left, left_pattern, diagonal_left_pattern), (right, right_pattern, diagonal_right_pattern))
        )
        diagonal_bays = math.floor((span - diagonal_offset) / (diagonal_pitch * diagonal_step) + 1e-9)
        if diagonal_bays < 1:
            minimum = bottom + top + diagonal_offset + diagonal_pitch * diagonal_step
            raise ResizeFailure(
                f"柱片高度至少需要 {minimum:g} mm 才能按所选孔位连接一根固定规格斜撑",
                "BRACE_RULE_TOO_SHORT", [{"minimumHeight": minimum}],
            )
        generated_count = max(holes - 1, 0) + max(diagonal_bays - 1, 0)
        if len(assembly["members"]) + generated_count > 150:
            raise ResizeFailure("撑杆规则将生成超过150个装配成员，请增大孔距或缩小柱片高度", "RESIZE_RESOURCE_LIMIT")

        last_slot_index = index_start + (holes - 1) * index_step
        if last_slot_index > 10000:
            raise ResizeFailure("撑杆规则请求的孔位编号超过10000，请检查孔距或接口编号起点", "BRACE_RULE_INTERFACE_MISSING")
        left_ids = {item.get("id") for item in left.get("interfaces", [])}
        right_ids = {item.get("id") for item in right.get("interfaces", [])}
        for index in range(holes):
            slot_index = index_start + index * index_step
            if _interface_id(left_pattern, slot_index) not in left_ids or _interface_id(right_pattern, slot_index) not in right_ids:
                raise ResizeFailure(f"柱片孔位接口不足，无法生成第 {index + 1} 层撑杆", "BRACE_RULE_INTERFACE_MISSING")
        for index in range(diagonal_bays):
            left_slot = diagonal_start + index * diagonal_step
            right_slot = left_slot + diagonal_step
            if left_slot > 10000 or right_slot > 10000:
                raise ResizeFailure("斜撑规则请求的孔位编号超过10000，请检查孔距或接口编号起点", "BRACE_RULE_INTERFACE_MISSING")
            endpoints = ((left, left_ids, left_pattern, diagonal_left_pattern, left_slot),
                         (right, right_ids, right_pattern, diagonal_right_pattern, right_slot))
            if rule.get('diagonalAlternate', True) and index % 2:
                endpoints = ((right, right_ids, right_pattern, diagonal_right_pattern, left_slot),
                             (left, left_ids, left_pattern, diagonal_left_pattern, right_slot))
            for column, identifiers, horizontal_pattern, diagonal_pattern, slot in endpoints:
                identifier = _interface_id(diagonal_pattern, slot)
                if identifier not in identifiers:
                    raise ResizeFailure(f"柱片孔位接口不足，无法生成第 {index + 1} 根斜撑", "BRACE_RULE_INTERFACE_MISSING")
                if geometries:
                    frames = geometries[column['id']].frames
                    node = frames.get(identifier)
                    anchor = frames.get(_interface_id(horizontal_pattern, index_start))
                    if node is None or anchor is None:
                        raise ResizeFailure("无法读取斜撑节点的真实孔位", "BRACE_RULE_INTERFACE_MISSING")
                    elevation = float(node['center'][2] - anchor['center'][2])
                    if elevation < -1e-6 or elevation > span + 1e-6:
                        raise ResizeFailure("所选斜撑孔位超出底部与顶部余量，请核对真实孔距和编号起点",
                                            "BRACE_RULE_RANGE_INVALID", [{'memberId': column['id'], 'interfaceId': identifier}])

        # Prototypes and their copies keep fixed geometry; their poses follow
        # actual node frames, including alternating diagonal directions.
        for index in range(holes):
            slot_index = index_start + index * index_step
            brace = horizontal if index == 0 else _copy_member(horizontal, rule_id, index, rule.get("generatedMemberPrefix", "generated-brace"), horizontal_pitch)
            _place_brace(brace, horizontal, left, right,
                         rule["horizontalLeftInterfaceId"], rule["horizontalRightInterfaceId"],
                         _interface_id(left_pattern, slot_index), _interface_id(right_pattern, slot_index), geometries)
            if index > 0:
                if brace["id"] in used_ids:
                    raise ResizeFailure(f"撑杆规则生成了重复成员标识：{brace['id']}", "BRACE_RULE_MEMBER_COLLISION")
                assembly["members"].append(brace)
                members[brace["id"]] = brace
                used_ids.add(brace["id"])
            connect([
                _mate(rule_id, index, "horizontal", "left", brace["id"], rule["horizontalLeftInterfaceId"], left["id"], _interface_id(left_pattern, slot_index), rule.get("horizontalRelation", "贴合")),
                _mate(rule_id, index, "horizontal", "right", brace["id"], rule["horizontalRightInterfaceId"], right["id"], _interface_id(right_pattern, slot_index), rule.get("horizontalRelation", "贴合")),
            ])

        for index in range(diagonal_bays):
            slot_index = diagonal_start + index * diagonal_step
            brace = diagonal if index == 0 else _copy_member(diagonal, rule_id, index, rule.get("generatedMemberPrefix", "generated-brace"), diagonal_pitch * diagonal_step)
            if index > 0:
                if brace["id"] in used_ids:
                    raise ResizeFailure(f"撑杆规则生成了重复成员标识：{brace['id']}", "BRACE_RULE_MEMBER_COLLISION")
                assembly["members"].append(brace)
                members[brace["id"]] = brace
                used_ids.add(brace["id"])
            left_column, right_column = left, right
            if rule.get("diagonalAlternate", True) and index % 2:
                left_column, right_column = right, left
            _place_brace(brace, diagonal, left_column, right_column,
                         rule["diagonalLeftInterfaceId"], rule["diagonalRightInterfaceId"],
                         _interface_id(diagonal_left_pattern if left_column is left else diagonal_right_pattern, slot_index),
                         _interface_id(diagonal_right_pattern if right_column is right else diagonal_left_pattern, slot_index + diagonal_step), geometries)
            connect([
                _mate(rule_id, index, "diagonal", "left", brace["id"], rule["diagonalLeftInterfaceId"], left_column["id"], _interface_id(diagonal_left_pattern if left_column is left else diagonal_right_pattern, slot_index), rule.get("diagonalRelation", "贴合")),
                _mate(rule_id, index, "diagonal", "right", brace["id"], rule["diagonalRightInterfaceId"], right_column["id"], _interface_id(diagonal_right_pattern if right_column is right else diagonal_left_pattern, slot_index + diagonal_step), rule.get("diagonalRelation", "贴合")),
            ])
    return geometric_relations
