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
            "partConnectionType": "螺栓连接", "partConnectionCustom": "",
            "componentConnectionType": "螺栓连接", "componentConnectionCustom": "",
            "designConstraints": "", "evidenceFiles": [],
        },
        "assembly": {"members": [], "mates": []}, "ports": [], "slots": [], "bindings": [],
        "parameters": [{"id": "componentLength", "label": "组件总长", "value": 2400, "unit": "mm", "min": 1000, "max": 6000}],
        "rules": [{"id": "rule-1", "expression": "组件总长必须在 1000~6000 mm 范围内"}],
    })


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
        data["assembly"]["members"].append(action.member.model_dump(by_alias=True))
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
        if not has_interface(document, port.source):
            raise ComponentError(422, "INVALID_REFERENCE")
        if any(item.id == port.id or item.source == port.source for item in document.ports):
            raise ComponentError(422, "DUPLICATE_PORT")
        data["ports"].append(port.model_dump(by_alias=True))
    elif action.type in ("parameter.value", "rule.expression"):
        collection, field = ("parameters", "value") if action.type == "parameter.value" else ("rules", "expression")
        item = next((item for item in data[collection] if item["id"] == action.id), None)
        if item is None:
            raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
        item[field] = getattr(action, field)
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

    return [
        check("definition", "组件名称", bool(document.definition.name.strip()), "请填写组件名称"),
        check("members", "装配成员", bool(members) and all(member.id.strip() for member in members) and len(member_ids) == len(members), "请加入零部件，成员标识需唯一"),
        check("references", "固定版本引用", bool(members) and all(member.template.publicationId.strip() and member.template.templateId.strip() and 0 < member.template.version <= 9007199254740991 and re.fullmatch(r"[a-fA-F0-9]{64}", member.template.sha256) for member in members), "成员需引用有效发布版本及模板包校验值"),
        check("mates", "内部连接数据", (len(members) < 2 or bool(mates)) and len({mate.id for mate in mates}) == len(mates) and all(has_interface(document, mate.from_) and has_interface(document, mate.to) and mate.from_.memberId != mate.to.memberId for mate in mates), "请检查连接两端的成员与接口；多成员组件需建立内部连接"),
        check("ports", "对外接口", bool(document.ports) and all(has_interface(document, port.source) for port in document.ports), "请继承有效的零部件接口"),
        check("parameters", "参数范围", bool(document.parameters) and len(parameter_ids) == len(document.parameters) and all(parameter.value is not None and parameter.min <= parameter.max and parameter.min <= parameter.value <= parameter.max for parameter in document.parameters), "参数需为范围内的有效数值"),
        check("bindings", "参数绑定来源", all(binding.parameterId in parameter_ids and any(member.id == binding.memberId and binding.memberParameterId in member.parameterValues for member in members) for binding in document.bindings), "参数绑定引用了不存在的组件参数或成员参数"),
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
            **{key: member[key] for key in ("name", "code", "role", "position", "rotation", "parameterValues", "interfaces")}, "bindings": bindings,
        })
    mates = [{
        "id": mate.id, "from": f"{mate.from_.memberId}.{mate.from_.interfaceId}", "to": f"{mate.to.memberId}.{mate.to.interfaceId}",
        "fromRef": mate.from_.model_dump(), "toRef": mate.to.model_dump(), "relation": mate.relation, "offset": mate.offset,
    } for mate in document.assembly.mates]
    ports = [{"id": port.id, "name": port.name, "sourceMemberId": port.source.memberId, "sourceInterfaceId": port.source.interfaceId,
              "source": port.source.model_dump(), "tags": port.tags} for port in document.ports]
    return {
        **{key: data[key] for key in ("schemaVersion", "templateKind", "revision", "unitSystem", "coordinateSystem")},
        "name": document.definition.name,
        "stages": {
            "definition": data["definition"], "assembly": {"members": members, "mates": mates}, "ports": ports,
            "slots": data["slots"], "parameters": {parameter.id: parameter.value for parameter in document.parameters},
            "parameterDefinitions": data["parameters"], "bindings": data["bindings"], "rules": data["rules"],
            "validation": [{"name": check["label"], "passed": check["status"] == "pass", **check} for check in validate_component(document)],
        },
    }
