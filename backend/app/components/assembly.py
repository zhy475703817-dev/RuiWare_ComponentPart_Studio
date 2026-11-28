"""Server-side assembly actions with pinned CAD and atomic revision checks."""
import asyncio
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from ..library import LibraryError
from .geometry_sources import load_sources
from .models import ComponentDocument, ComponentAction, PartInterface, MemberParameterDefinition
from .interface_compatibility import assess_interface_compatibility
from .resize_geometry import CadProvider, exact_bounds, matrix
from .resize_solver import flatten, ResizeFailure
from .service import ComponentError, apply_action


def read_document(app, draft_id, revision):
    draft = app.state.drafts.get(draft_id)
    if draft["document"]["revision"] != revision:
        raise ComponentError(409, "REVISION_CONFLICT", draft)
    return ComponentDocument.model_validate(draft["document"])


def find_member(document, member_id):
    member = next((item for item in document.assembly.members if item.id == member_id), None)
    if member is None:
        raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND")
    return member


def connected_ids(document, member_id):
    result, queue = {member_id}, [member_id]
    while queue:
        current = queue.pop()
        for mate in document.assembly.mates:
            a, b = mate.from_.memberId, mate.to.memberId
            neighbor = b if a == current else a if b == current else None
            if neighbor is not None and neighbor not in result:
                result.add(neighbor)
                queue.append(neighbor)
    return result


async def geometry(app, document):
    try:
        templates, publications = await load_sources(app, document.model_dump(by_alias=True))
    except ResizeFailure as error:
        if isinstance(error.__cause__, LibraryError):
            raise error.__cause__
        raise ComponentError(422, error.code) from None

    def measure():
        leaves, endpoints, _, _ = flatten(document.model_dump(by_alias=True), publications)
        provider = CadProvider(templates)
        boxes, frames = {}, {}
        for leaf in leaves:
            member, path, transform = leaf["member"], leaf["path"], leaf["world"]
            shape = provider.evaluate(member["template"], member["parameterValues"])
            box = exact_bounds(shape.shape, transform)
            root_id = path[0]
            boxes[root_id] = np.array([np.minimum(boxes[root_id][0], box[0]), np.maximum(boxes[root_id][1], box[1])]) if root_id in boxes else box
            declarations = {item["id"]: PartInterface.model_validate(item) for item in shape.interfaces}
            context = SimpleNamespace(parameterValues={**{p["id"]: p["default"] for p in shape.definitions}, **member["parameterValues"]},
                parameterDefinitions=[MemberParameterDefinition.model_validate(p) for p in shape.definitions])
            for identifier, frame in shape.frames.items():
                frames[(path, identifier)] = {"center": transform[:3, :3] @ frame["center"] + transform[:3, 3],
                    "normal": transform[:3, :3] @ frame["normal"], "interface": declarations.get(identifier), "context": context}
        return boxes, {key: frames[value] for key, value in endpoints.items() if value in frames}

    try:
        async with app.state.reconstruction_slots:
            return await asyncio.to_thread(measure)
    except (ValueError, RuntimeError) as error:
        if isinstance(error, ResizeFailure):
            raise ComponentError(422, error.code) from None
        raise ComponentError(422, "ASSEMBLY_GEOMETRY_UNAVAILABLE") from None


def component_member(publication):
    document = publication["document"]
    definitions = []
    integer_ids = {p["countParameterId"] for p in document["patterns"]}
    for parameter in document["parameters"]:
        low, high = parameter["min"], parameter["max"]
        for binding in document["bindings"]:
            if binding["parameterId"] != parameter["id"]:
                continue
            member = next((m for m in document["assembly"]["members"] if m["id"] == binding["memberId"]), {})
            definition = next((p for p in member.get("parameterDefinitions", []) if p["id"] == binding["memberParameterId"] and p["unit"] == parameter["unit"] and p["valueType"] in ("number", "integer")), None)
            if definition:
                low = max(low, definition["minimum"]) if definition["minimum"] is not None else low
                high = min(high, definition["maximum"]) if definition["maximum"] is not None else high
        integer = parameter["id"] in integer_ids
        if integer:
            low, high = float(np.ceil(low)), float(np.floor(high))
        if low > high:
            raise ComponentError(422, "INVALID_BINDING_RANGE")
        value = parameter["value"] if parameter["value"] is not None else low
        definitions.append({"id": parameter["id"], "label": parameter["label"], "unit": parameter["unit"],
            "valueType": "integer" if integer else "number", "minimum": low, "maximum": high,
            "default": min(high, max(low, float(np.floor(value+.5)) if integer else value))})
    interfaces = []
    for port in document["ports"]:
        source = next((m for m in document["assembly"]["members"] if m["id"] == port["source"]["memberId"]), {})
        item = port.get("interface") or next((i for i in source.get("interfaces", []) if i["id"] == port["source"]["interfaceId"]), None)
        if item:
            interfaces.append({**deepcopy(item), "id": port["id"], "name": port["name"], "tags": port["tags"]})
    return {"id": str(uuid4()), "name": publication["name"], "code": publication["code"], "role": publication["name"],
        "template": {"templateKind": "component", **{k: publication[k] for k in ("publicationId", "templateId", "version", "sha256")}},
        "position": [0, 0, 0], "rotation": [0, 0, 0], "parameterValues": {p["id"]: p["value"] for p in document["parameters"] if p["value"] is not None},
        "parameterDefinitions": definitions, "interfaces": interfaces}


async def add_template_member(app, draft_id, revision, publication_id, sha256, kind, position=None):
    document = read_document(app, draft_id, revision)
    actions = []
    if kind == "component":
        publication = await asyncio.to_thread(app.state.publications.detail, publication_id)
        if publication["sha256"].lower() != sha256.lower():
            raise ComponentError(409, "PUBLICATION_HASH_MISMATCH")
        member = component_member(publication)
        actions.append({"type": "interface.types.merge", "types": publication["document"].get("interfaceTypes", [])})
    else:
        from ..reconstruction import _read_template, editable_parameters
        publication, draft, material = await _read_template(app.state, publication_id, sha256)
        values = {p["id"]: p["default"] for p in editable_parameters(draft)}
        try:
            async with app.state.reconstruction_slots:
                shape = await asyncio.to_thread(CadProvider({(publication_id, sha256.lower()): (draft, material)}).evaluate,
                    {"publicationId": publication_id, "sha256": sha256}, values)
        except (ValueError, RuntimeError):
            raise ComponentError(422, "ASSEMBLY_GEOMETRY_UNAVAILABLE") from None
        member = {"id": str(uuid4()), "name": publication.name, "code": publication.code, "role": publication.name,
            "template": {"publicationId": publication_id, "templateId": publication.templateId, "version": publication.version, "sha256": sha256},
            "position": [0, 0, 0], "rotation": [0, 0, 0], "parameterValues": {p["id"]: p["default"] for p in shape.definitions},
            "parameterDefinitions": shape.definitions, "interfaces": shape.interfaces, "resolvedInterfaces": shape.resolved}
    if position is not None:
        member["position"] = position
    actions.append({"type": "member.add", "member": member})
    if position is None:
        projected = document
        for action in actions:
            projected = apply_action(projected, ComponentAction.validate_python(action))
        boxes, _ = await geometry(app, projected)
        box = boxes.get(member["id"])
        if box is not None:
            others = [b for identifier, b in boxes.items() if identifier != member["id"]]
            x = max(b[1, 0] for b in others) + max(20, (box[1, 0]-box[0, 0])*.1) - box[0, 0] if others else -float((box[0, 0]+box[1, 0])/2)
            actions.append({"type": "member.patch", "id": member["id"], "patch": {
                "position": [float(x), -float((box[0, 1]+box[1, 1])/2), -float(box[0, 2])]}})
    return await asyncio.to_thread(app.state.drafts.update, draft_id, revision, actions, actor="agent")


def copy_member(app, draft_id, revision, member_id):
    document = read_document(app, draft_id, revision)
    member = find_member(document, member_id).model_dump(by_alias=True)
    member.update(id=str(uuid4()), name=member["name"]+" 副本", role=member["role"]+" 副本",
                  position=[member["position"][0]+40, *member["position"][1:]])
    # Reuse the validated model snapshot action to preserve all existing values;
    # member.add infers dimensions and creates bindings for newly imported parts.
    data = document.model_dump(by_alias=True)
    model = {key: data[key] for key in ("assembly", "ports", "bindings", "patterns", "slots")}
    model["assembly"]["members"].append(member)
    return app.state.drafts.update(draft_id, revision, {"type": "model.restore", "model": model}, actor="agent")


async def ground_member(app, draft_id, revision, member_id):
    document = read_document(app, draft_id, revision)
    find_member(document, member_id)
    boxes, _ = await geometry(app, document)
    if member_id not in boxes:
        raise ComponentError(422, "ASSEMBLY_GEOMETRY_UNAVAILABLE")
    delta = -float(boxes[member_id][0, 2])
    group = connected_ids(document, member_id)
    actions = [{"type": "member.patch", "id": member.id, "patch": {
        "position": [*member.position[:2], member.position[2]+delta]}} for member in document.assembly.members if member.id in group]
    return await asyncio.to_thread(app.state.drafts.update, draft_id, revision, actions, actor="agent")


async def connect_interfaces(app, draft_id, revision, source, target, relation):
    document = read_document(app, draft_id, revision)
    find_member(document, source.memberId)
    find_member(document, target.memberId)
    group = connected_ids(document, target.memberId)
    if source.memberId in group:
        raise ComponentError(422, "ASSEMBLY_ALREADY_CONNECTED")
    mate = {"type": "mate.add", "mate": {"id": str(uuid4()), "from": source.model_dump(), "to": target.model_dump(), "relation": relation, "offset": 0}}
    # Validate compatibility and occupancy before any CAD work or writes.
    apply_action(document, ComponentAction.validate_python(mate))
    _, frames = await geometry(app, document)
    a = frames.get(((source.memberId,), source.interfaceId))
    b = frames.get(((target.memberId,), target.interfaceId))
    if a is None or b is None:
        raise ComponentError(422, "ASSEMBLY_INTERFACE_FRAME_MISSING")
    assessment = assess_interface_compatibility(a["interface"], b["interface"], relation, a["context"], b["context"])
    if not assessment.compatible:
        raise ComponentError(422, "INCOMPATIBLE_INTERFACE" if assessment.status == "incompatible" else "INTERFACE_REQUIRES_REVIEW",
            [{"code": item.code, "status": item.status, "message": item.message} for item in assessment.checks])
    for endpoint, frame in ((source, a), (target, b)):
        usage = sum(1 for existing in document.assembly.mates if existing.from_ == endpoint or existing.to == endpoint)
        if usage >= frame["interface"].mating.capacity:
            raise ComponentError(422, "ENDPOINT_ALREADY_CONSUMED")
    correction = Rotation.align_vectors([-a["normal"]], [b["normal"]])[0].as_matrix()
    delta = np.eye(4)
    delta[:3, :3] = correction
    delta[:3, 3] = a["center"] - correction @ b["center"]
    actions = [mate]
    for member in document.assembly.members:
        if member.id not in group:
            continue
        transform = delta @ matrix(member.position, member.rotation)
        actions.append({"type": "member.patch", "id": member.id, "patch": {
            "position": transform[:3, 3].tolist(), "rotation": Rotation.from_matrix(transform[:3, :3]).as_euler("XYZ").tolist()}})
    return await asyncio.to_thread(app.state.drafts.update, draft_id, revision, actions, actor="agent")
