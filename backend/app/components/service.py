import math
import re

from .models import ComponentAction, ComponentDocument, InterfaceSource, TypedAction


class ComponentError(Exception):
    def __init__(self, status: int, code: str, current: dict | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.current = current

    def as_dict(self) -> dict:
        error = {"code": self.code}
        if self.current is not None:
            error["current"] = self.current
        return {"error": error}


def create_component_document() -> ComponentDocument:
    return ComponentDocument.model_validate({
        "schemaVersion": "component-1.0", "templateKind": "component", "revision": 1,
        "unitSystem": "mm-kg-s", "coordinateSystem": "right-handed-z-up",
        "definition": {
            "name": "横梁组件", "code": "", "category": "货架组件", "description": "由多个已发布零部件组成的可复用组件模板",
            "projectCode": "", "owner": "", "organization": "", "searchTags": [],
            "connectionForms": {
                "internal": {"mode": "preset", "value": "螺栓连接", "details": ""},
                "interComponent": {"mode": "preset", "value": "插接/卡扣", "details": ""},
            },
            "designConstraints": "", "evidence": [],
        },
        "assembly": {"members": [], "mates": []}, "ports": [], "slots": [], "bindings": [], "patterns": [],
        "parameters": [
            {"id": "componentLength", "label": "组件总长", "value": 2400, "unit": "mm", "min": 1000, "max": 6000},
            {"id": "componentWidth", "label": "组件总宽", "value": 600, "unit": "mm", "min": 100, "max": 3000},
            {"id": "componentHeight", "label": "组件总高", "value": 1800, "unit": "mm", "min": 300, "max": 8000},
        ],
        "rules": [{"id": "rule-1", "kind": "constraint", "label": "工程限制", "expression": "组件总长必须在 1000~6000 mm 范围内"}],
    })


DIMENSION_ALIASES = {
    "length": ("componentLength", "length", "overallLength", "总长", "长度"),
    "width": ("componentWidth", "width", "overallWidth", "总宽", "宽度"),
    "height": ("componentHeight", "height", "overallHeight", "总高", "高度"),
}


def _numeric_member_value(member: dict, aliases: tuple[str, ...]) -> float | None:
    for identifier, value in member.get("parameterValues", {}).items():
        if identifier.lower() in {alias.lower() for alias in aliases} and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    for definition in member.get("parameterDefinitions", []):
        if not any(definition.get(key, "").lower() in {alias.lower() for alias in aliases} for key in ("id", "label")):
            continue
        value = member.get("parameterValues", {}).get(definition["id"], definition.get("default"))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None


def _member_extent(member: dict, axis: int) -> tuple[float, float] | None:
    axes = (DIMENSION_ALIASES["length"], DIMENSION_ALIASES["width"], DIMENSION_ALIASES["height"])
    dimensions = [_numeric_member_value(member, aliases) for aliases in axes]
    if dimensions[axis] is None:
        return None
    half = [max(0.0, value) / 2 if value is not None else 0.0 for value in dimensions]
    x, y, z = member.get("rotation", [0, 0, 0])
    a, b, c, d, e, f = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rotation = (
        (c * e, -c * f, d),
        (a * f + b * d * e, -a * e + b * d * f, -b * c),
        (b * f - a * d * e, b * e + a * d * f, a * c),
    )
    radius = sum(abs(factor) * half[index] for index, factor in enumerate(rotation[axis]))
    center = float(member.get("position", [0, 0, 0])[axis])
    return center - radius, center + radius


def _infer_envelope_dimensions(parameters: list[dict], members: list[dict], patterns: list[dict]) -> dict[str, float | None]:
    expanded = []
    by_id = {parameter["id"]: parameter for parameter in parameters}
    for member in members:
        expanded.append(member)
        for pattern in patterns:
            if member["id"] not in pattern["memberIds"]:
                continue
            count = max(1, round(by_id.get(pattern["countParameterId"], {}).get("value") or 1))
            spacing = by_id.get(pattern["spacingParameterId"], {}).get("value") or 0
            axis = {"x": 0, "y": 1, "z": 2}[pattern["axis"]]
            for index in range(1, count):
                copy = {**member, "position": list(member["position"])}
                copy["position"][axis] += spacing * index
                expanded.append(copy)
    values: list[float | None] = []
    for axis in range(3):
        extents = [extent for member in expanded if (extent := _member_extent(member, axis)) is not None]
        values.append(max(0.0, max(end for _, end in extents) - min(start for start, _ in extents)) if extents else None)
    return dict(zip(("length", "width", "height"), values))


def has_interface(document: ComponentDocument, source: InterfaceSource) -> bool:
    return any(member.id == source.memberId and any(item.id == source.interfaceId for item in member.interfaces)
               for member in document.assembly.members)


def apply_action(document: ComponentDocument, action: TypedAction) -> ComponentDocument:
    action = ComponentAction.validate_python(action)
    data = document.model_dump(by_alias=True)
    if action.type == "reset":
        data = create_component_document().model_dump(by_alias=True)
        data["revision"] = document.revision
    elif action.type == "definition.patch":
        data["definition"].update(action.patch.model_dump(exclude_unset=True))
    elif action.type == "member.add":
        if any(member.id == action.member.id for member in document.assembly.members):
            raise ComponentError(422, "DUPLICATE_ID")
        previous_inferred = _infer_envelope_dimensions(data["parameters"], data["assembly"]["members"], data["patterns"])
        member = action.member.model_dump(by_alias=True)
        data["assembly"]["members"].append(member)
        existing = {(item["parameterId"], item["memberId"], item["memberParameterId"]) for item in data["bindings"]}
        for parameter in data["parameters"]:
            parameter_text = f"{parameter['id']} {parameter['label']}".lower()
            axis = "length" if "length" in parameter_text or "长" in parameter_text else "width" if "width" in parameter_text or "宽" in parameter_text else "height" if "height" in parameter_text or "高" in parameter_text else None
            names = DIMENSION_ALIASES.get(axis, ())
            definition = next((item for item in member.get("parameterDefinitions", [])
                               if item.get("unit") == parameter["unit"] and item.get("valueType") in ("number", "integer")
                               and any(alias.lower() in item.get(key, "").lower() for key in ("id", "label") for alias in names)), None)
            if definition is None:
                continue
            if not document.assembly.members and parameter["value"] is not None and isinstance(definition.get("default"), (int, float)) and not isinstance(definition.get("default"), bool):
                minimum = max(parameter["min"], definition.get("minimum") if definition.get("minimum") is not None else float("-inf"))
                maximum = min(parameter["max"], definition.get("maximum") if definition.get("maximum") is not None else float("inf"))
                parameter["value"] = min(maximum, max(minimum, definition["default"]))
            key = (parameter["id"], member["id"], definition["id"])
            if key not in existing:
                data["bindings"].append({"id": f"{member['id']}:{parameter['id']}:{definition['id']}", "parameterId": parameter["id"], "memberId": member["id"], "memberParameterId": definition["id"]})
                existing.add(key)
        inferred = _infer_envelope_dimensions(data["parameters"], data["assembly"]["members"], data["patterns"])
        defaults = {"componentLength": 2400, "componentWidth": 600, "componentHeight": 1800}
        for parameter in data["parameters"]:
            parameter_text = f"{parameter['id']} {parameter['label']}".lower()
            axis = "length" if "length" in parameter_text or "长" in parameter_text else "width" if "width" in parameter_text or "宽" in parameter_text else "height" if "height" in parameter_text or "高" in parameter_text else None
            value = inferred.get(axis) if axis else None
            previous_value = previous_inferred.get(axis) if axis else None
            untouched = defaults.get(parameter["id"]) == parameter["value"] or parameter["value"] is not None and previous_value is not None and abs(previous_value - parameter["value"]) < 1e-8
            if value is not None and untouched:
                parameter["value"] = min(parameter["max"], max(parameter["min"], value))
        member["parameterValues"] = dict(member.get("parameterValues", {}))
        for binding in data["bindings"]:
            if binding["memberId"] != member["id"]:
                continue
            parameter = next(item for item in data["parameters"] if item["id"] == binding["parameterId"])
            definition = next(item for item in member.get("parameterDefinitions", []) if item["id"] == binding["memberParameterId"])
            if parameter["value"] is None:
                continue
            minimum = max(parameter["min"], definition.get("minimum") if definition.get("minimum") is not None else float("-inf"))
            maximum = min(parameter["max"], definition.get("maximum") if definition.get("maximum") is not None else float("inf"))
            if minimum <= maximum:
                value = round(parameter["value"]) if definition.get("valueType") == "integer" else parameter["value"]
                member["parameterValues"][definition["id"]] = min(maximum, max(minimum, value))
    elif action.type in ("member.patch", "member.remove"):
        member = next((member for member in data["assembly"]["members"] if member["id"] == action.id), None)
        if member is None:
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        if action.type == "member.patch":
            member.update(action.patch.model_dump(exclude_unset=True))
        else:
            data["assembly"]["members"] = [item for item in data["assembly"]["members"] if item["id"] != action.id]
            data["assembly"]["mates"] = [item for item in data["assembly"]["mates"] if item["from"]["memberId"] != action.id and item["to"]["memberId"] != action.id]
            data["ports"] = [item for item in data["ports"] if item["source"]["memberId"] != action.id]
            data["bindings"] = [item for item in data["bindings"] if item["memberId"] != action.id]
            data["patterns"] = [
                {**pattern, "memberIds": [identifier for identifier in pattern["memberIds"] if identifier != action.id]}
                for pattern in data["patterns"] if any(identifier != action.id for identifier in pattern["memberIds"])
            ]
            for slot in data["slots"]:
                if slot["memberId"] == action.id:
                    slot["memberId"] = None
    elif action.type == "mate.add":
        mate = action.mate
        if not has_interface(document, mate.from_) or not has_interface(document, mate.to) or mate.from_.memberId == mate.to.memberId:
            raise ComponentError(422, "INVALID_REFERENCE")
        if any(item.id == mate.id or (item.from_ == mate.from_ and item.to == mate.to) for item in document.assembly.mates):
            raise ComponentError(422, "DUPLICATE_CONNECTION")
        data["assembly"]["mates"].append(mate.model_dump(by_alias=True))
    elif action.type == "port.add":
        port = action.port
        source_member = next((member for member in document.assembly.members if member.id == port.source.memberId), None)
        source_interface = next((item for item in (source_member.interfaces if source_member else []) if item.id == port.source.interfaceId), None)
        if source_interface is None:
            raise ComponentError(422, "INVALID_REFERENCE")
        if any(item.id == port.id or item.source == port.source for item in document.ports):
            raise ComponentError(422, "DUPLICATE_PORT")
        # New clients send the source declaration as a snapshot so physical
        # size, mode, and geometry references remain visible after inheritance.
        # Reject a mismatched snapshot instead of publishing stale metadata.
        if port.interface is not None and port.interface != source_interface:
            raise ComponentError(422, "INTERFACE_SNAPSHOT_MISMATCH")
        data["ports"].append({
            **port.model_dump(by_alias=True),
            "interface": source_interface.model_dump(by_alias=True),
        })
    elif action.type == "binding.set":
        data["bindings"] = [item for item in data["bindings"] if item["id"] != action.id]
        if action.binding is not None:
            if action.binding.id != action.id:
                raise ComponentError(422, "INVALID_BINDING")
            parameter = next((item for item in document.parameters if item.id == action.binding.parameterId), None)
            member = next((item for item in document.assembly.members if item.id == action.binding.memberId), None)
            definition = next((item for item in (member.parameterDefinitions if member else []) if item.id == action.binding.memberParameterId), None)
            if parameter is None or member is None or definition is None or definition.valueType not in ("number", "integer") or definition.unit != parameter.unit:
                raise ComponentError(422, "INVALID_BINDING")
            data["bindings"].append(action.binding.model_dump())
            targets = [item for item in data["bindings"] if item["parameterId"] == parameter.id]
            target_definitions = []
            for target_binding in targets:
                target_member = next(entry for entry in data["assembly"]["members"] if entry["id"] == target_binding["memberId"])
                target_definition = next((entry for entry in target_member.get("parameterDefinitions", []) if entry["id"] == target_binding["memberParameterId"]), None)
                if target_definition is None or target_definition.get("unit") != parameter.unit or target_definition.get("valueType") not in ("number", "integer"):
                    raise ComponentError(422, "INVALID_BINDING")
                target_definitions.append(target_definition)
            minimum = max([parameter.min, *(item["minimum"] for item in target_definitions if item.get("minimum") is not None)])
            maximum = min([parameter.max, *(item["maximum"] for item in target_definitions if item.get("maximum") is not None)])
            integer_bound = any(item["valueType"] == "integer" for item in target_definitions)
            lower = math.ceil(minimum) if integer_bound else minimum
            upper = math.floor(maximum) if integer_bound else maximum
            if lower > upper:
                raise ComponentError(422, "INVALID_BINDING_RANGE")
            parameter_data = next(item for item in data["parameters"] if item["id"] == parameter.id)
            if parameter_data["value"] is not None:
                current = math.floor(parameter_data["value"] + 0.5) if integer_bound else parameter_data["value"]
                parameter_data["value"] = min(upper, max(lower, current))
                for target_binding in targets:
                    target_member = next(entry for entry in data["assembly"]["members"] if entry["id"] == target_binding["memberId"])
                    target_member["parameterValues"][target_binding["memberParameterId"]] = parameter_data["value"]
    elif action.type == "parameter.add":
        if any(item["id"] == action.parameter.id for item in data["parameters"]):
            raise ComponentError(422, "DUPLICATE_ID")
        if action.parameter.min > action.parameter.max or action.parameter.value is None or not action.parameter.min <= action.parameter.value <= action.parameter.max:
            raise ComponentError(422, "INVALID_PARAMETER_RANGE")
        data["parameters"].append(action.parameter.model_dump())
    elif action.type == "parameter.remove":
        if not any(item["id"] == action.id for item in data["parameters"]):
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        data["parameters"] = [item for item in data["parameters"] if item["id"] != action.id]
        data["bindings"] = [item for item in data["bindings"] if item["parameterId"] != action.id]
        data["patterns"] = [item for item in data["patterns"] if item["countParameterId"] != action.id and item["spacingParameterId"] != action.id]
    elif action.type == "pattern.add":
        pattern = action.pattern
        parameter_by_id = {item.id: item for item in document.parameters}
        count = parameter_by_id.get(pattern.countParameterId)
        spacing = parameter_by_id.get(pattern.spacingParameterId)
        member_ids = list(dict.fromkeys(pattern.memberIds))
        member_id_set = {member.id for member in document.assembly.members}
        used_member_ids = {identifier for item in document.patterns for identifier in item.memberIds}
        if (not pattern.id.strip() or not pattern.label.strip() or any(item.id == pattern.id for item in document.patterns)
                or not member_ids or any(identifier not in member_id_set or identifier in used_member_ids for identifier in member_ids)
                or count is None or count.unit not in ("个", "件") or count.value is None or not count.value.is_integer() or count.min < 1 or count.value < count.min or count.value > count.max
                or spacing is None or spacing.unit != "mm" or spacing.value is None or spacing.value < 0 or spacing.min < 0 or spacing.value < spacing.min or spacing.value > spacing.max):
            raise ComponentError(422, "INVALID_PATTERN")
        data["patterns"].append({**pattern.model_dump(), "memberIds": member_ids})
    elif action.type == "pattern.remove":
        if not any(item["id"] == action.id for item in data["patterns"]):
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        data["patterns"] = [item for item in data["patterns"] if item["id"] != action.id]
    elif action.type == "rule.add":
        rule = action.rule.model_dump()
        if not rule["id"].strip() or not rule["expression"].strip() or any(item["id"] == rule["id"] for item in data["rules"]):
            raise ComponentError(422, "INVALID_RULE")
        data["rules"].append(rule)
    elif action.type == "rule.remove":
        if not any(item["id"] == action.id for item in data["rules"]):
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        data["rules"] = [item for item in data["rules"] if item["id"] != action.id]
    elif action.type in ("parameter.value", "rule.expression"):
        collection, field = ("parameters", "value") if action.type == "parameter.value" else ("rules", "expression")
        item = next((item for item in data[collection] if item["id"] == action.id), None)
        if item is None:
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        if action.type == "parameter.value" and action.value is not None:
            definitions = []
            for binding in data["bindings"]:
                if binding["parameterId"] != action.id:
                    continue
                member = next((entry for entry in data["assembly"]["members"] if entry["id"] == binding["memberId"]), None)
                definition = next((entry for entry in (member or {}).get("parameterDefinitions", []) if entry["id"] == binding["memberParameterId"] and entry.get("unit") == item["unit"] and entry.get("valueType") in ("number", "integer")), None)
                if definition is not None:
                    definitions.append(definition)
            lower = max([item["min"], *(definition["minimum"] for definition in definitions if definition.get("minimum") is not None)])
            upper = min([item["max"], *(definition["maximum"] for definition in definitions if definition.get("maximum") is not None)])
            integer_bound = any(definition["valueType"] == "integer" for definition in definitions)
            if integer_bound:
                lower, upper = math.ceil(lower), math.floor(upper)
            if lower > upper:
                raise ComponentError(422, "INVALID_BINDING_RANGE")
            candidate = math.floor(action.value + 0.5) if integer_bound else action.value
            item[field] = min(upper, max(lower, candidate))
        else:
            item[field] = getattr(action, field)
        if action.type == "parameter.value":
            for binding in data["bindings"]:
                if binding["parameterId"] != action.id:
                    continue
                member = next((entry for entry in data["assembly"]["members"] if entry["id"] == binding["memberId"]), None)
                definition = next((entry for entry in (member or {}).get("parameterDefinitions", []) if entry["id"] == binding["memberParameterId"] and entry.get("unit") == item["unit"] and entry.get("valueType") in ("number", "integer")), None)
                if member is not None:
                    if action.value is None:
                        member["parameterValues"].pop(binding["memberParameterId"], None)
                    elif definition is not None:
                        minimum = max(item["min"], definition["minimum"]) if definition.get("minimum") is not None else item["min"]
                        maximum = min(item["max"], definition["maximum"]) if definition.get("maximum") is not None else item["max"]
                        member["parameterValues"][binding["memberParameterId"]] = min(maximum, max(minimum, item[field]))
    if data == document.model_dump(by_alias=True):
        return document
    data["revision"] = document.revision + 1
    return ComponentDocument.model_validate(data)


def validate_component(document: ComponentDocument) -> list[dict]:
    members, mates = document.assembly.members, document.assembly.mates
    member_ids = {member.id for member in members}
    parameter_ids = {parameter.id for parameter in document.parameters}

    def check(identifier, label, passed, failure):
        return {"id": identifier, "label": label, "status": "pass" if passed else "fail", "message": "基础数据检查通过" if passed else failure}

    def binding_valid(binding):
        parameter = next((item for item in document.parameters if item.id == binding.parameterId), None)
        member = next((item for item in members if item.id == binding.memberId), None)
        definition = next((item for item in (member.parameterDefinitions if member else []) if item.id == binding.memberParameterId), None)
        if parameter is None or member is None or definition is None or binding.memberParameterId not in member.parameterValues:
            return False
        if definition.valueType not in ("number", "integer") or definition.unit != parameter.unit:
            return False
        minimum = max(parameter.min, definition.minimum) if definition.minimum is not None else parameter.min
        maximum = min(parameter.max, definition.maximum) if definition.maximum is not None else parameter.max
        if definition.valueType == "integer":
            minimum, maximum = math.ceil(minimum), math.floor(maximum)
        return minimum <= maximum

    def pattern_valid(pattern):
        count = next((item for item in document.parameters if item.id == pattern.countParameterId), None)
        spacing = next((item for item in document.parameters if item.id == pattern.spacingParameterId), None)
        return bool(pattern.memberIds and len(set(pattern.memberIds)) == len(pattern.memberIds)
                    and all(identifier in member_ids for identifier in pattern.memberIds)
                    and count is not None and count.unit in ("件", "个") and count.value is not None and count.value.is_integer()
                    and count.min >= 1 and count.min <= count.value <= count.max
                    and spacing is not None and spacing.unit == "mm" and spacing.value is not None
                    and spacing.min >= 0 and spacing.min <= spacing.value <= spacing.max and spacing.value >= 0)

    return [
        check("definition", "组件名称", bool(document.definition.name.strip()), "请填写组件名称"),
        check("members", "装配成员", bool(members) and all(member.id.strip() for member in members) and len(member_ids) == len(members), "请加入零部件，成员标识需唯一"),
        check("references", "固定版本引用", bool(members) and all(member.template.publicationId.strip() and member.template.templateId.strip() and 0 < member.template.version <= 9007199254740991 and re.fullmatch(r"[a-fA-F0-9]{64}", member.template.sha256) for member in members), "成员需引用有效发布版本及模板包校验值"),
        check("mates", "内部连接数据", (len(members) < 2 or bool(mates)) and len({mate.id for mate in mates}) == len(mates) and all(has_interface(document, mate.from_) and has_interface(document, mate.to) and mate.from_.memberId != mate.to.memberId for mate in mates), "请检查连接两端的成员与接口；多成员组件需建立内部连接"),
        check("ports", "对外接口", bool(document.ports) and all(
            has_interface(document, port.source) and (port.interface is None or port.interface == next(
                item for member in members if member.id == port.source.memberId for item in member.interfaces if item.id == port.source.interfaceId
            )) for port in document.ports), "请继承有效的零部件接口，并检查接口信息是否仍与来源一致"),
        check("parameters", "参数范围", bool(document.parameters) and len(parameter_ids) == len(document.parameters) and all(parameter.value is not None and parameter.min <= parameter.max and parameter.min <= parameter.value <= parameter.max for parameter in document.parameters), "参数需为范围内的有效数值"),
        check("bindings", "参数绑定来源", all(binding_valid(binding) for binding in document.bindings) and all(
            max([parameter.min, *(definition.minimum for binding in document.bindings if binding.parameterId == parameter.id
                for member in members if member.id == binding.memberId
                for definition in member.parameterDefinitions if definition.id == binding.memberParameterId
                and definition.minimum is not None)])
            <= min([parameter.max, *(definition.maximum for binding in document.bindings if binding.parameterId == parameter.id
                for member in members if member.id == binding.memberId
                for definition in member.parameterDefinitions if definition.id == binding.memberParameterId
                and definition.maximum is not None)])
            for parameter in document.parameters), "参数绑定引用无效，或与成员参数范围没有交集"),
        check("patterns", "组件构成规则", all(pattern_valid(pattern) for pattern in document.patterns)
              and len({identifier for pattern in document.patterns for identifier in pattern.memberIds}) == sum(len(pattern.memberIds) for pattern in document.patterns), "重复构成规则引用无效成员、参数或重叠成员"),
        check("slots", "槽位引用", all((not slot.required) if slot.memberId is None else slot.memberId in member_ids for slot in document.slots), "必填槽位未放置成员或引用了失效成员"),
        {"id": "assembly.solve", "label": "装配求解", "status": "pending", "message": "尚未接入装配求解，连接数据不会自动改变成员位置"},
        {"id": "rules.execution", "label": "工程规则执行", "status": "pending", "message": "当前仅保存规则说明，尚未接入规则执行"},
    ]


def serialize_component(document: ComponentDocument) -> dict:
    data = document.model_dump(by_alias=True)
    members = []
    for member in data["assembly"]["members"]:
        template = member["template"]
        bindings = {**member["parameterValues"], **{binding.memberParameterId: binding.parameterId for binding in document.bindings if binding.memberId == member["id"]}}
        members.append({
            "memberId": member["id"], "partTemplateVersion": template["publicationId"], "partTemplateRef": template,
            **{key: member.get(key, []) if key in ("parameterDefinitions", "resolvedInterfaces") else member[key] for key in ("name", "code", "role", "position", "rotation", "parameterValues", "parameterDefinitions", "resolvedInterfaces", "interfaces")}, "bindings": bindings,
        })
    mates = [{
        "id": mate.id, "from": f"{mate.from_.memberId}.{mate.from_.interfaceId}", "to": f"{mate.to.memberId}.{mate.to.interfaceId}",
        "fromRef": mate.from_.model_dump(), "toRef": mate.to.model_dump(), "relation": mate.relation, "offset": mate.offset,
    } for mate in document.assembly.mates]
    ports = []
    for port in document.ports:
        source_member = next((member for member in document.assembly.members if member.id == port.source.memberId), None)
        source_interface = next((item for item in (source_member.interfaces if source_member else []) if item.id == port.source.interfaceId), None)
        ports.append({
            "id": port.id, "name": port.name, "sourceMemberId": port.source.memberId,
            "sourceInterfaceId": port.source.interfaceId, "source": port.source.model_dump(), "tags": port.tags,
            "interface": (port.interface or source_interface).model_dump(by_alias=True) if port.interface or source_interface else None,
        })
    return {
        **{key: data[key] for key in ("schemaVersion", "templateKind", "revision", "unitSystem", "coordinateSystem")},
        "name": document.definition.name,
        "stages": {
            "definition": data["definition"], "assembly": {"members": members, "mates": mates}, "ports": ports,
            "slots": data["slots"], "parameters": {parameter.id: parameter.value for parameter in document.parameters},
            "parameterDefinitions": data["parameters"], "bindings": data["bindings"], "patterns": data["patterns"], "rules": data["rules"],
            "validation": [{"name": check["label"], "passed": check["status"] == "pass", **check} for check in validate_component(document)],
        },
    }
