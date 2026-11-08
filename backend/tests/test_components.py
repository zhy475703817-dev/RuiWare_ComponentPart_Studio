import json
import hashlib
from io import BytesIO
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.storage_helpers import MemoryMinio, STORAGE_ENV


def member(identifier):
    return {
        "id": identifier, "name": "横梁", "code": "BEAM", "role": identifier,
        "template": {"publicationId": "version.1", "templateId": "part.1", "version": 3, "sha256": "a" * 64},
        "position": [0, 0, 0], "rotation": [0, 0, 0], "parameterValues": {"length": 2400},
        "interfaces": [{"id": "face.left", "name": "左端面", "type": "plane", "tags": ["end-face"], "referenceFrame": {"axis": "z"}, "mating": {
            "version": 1, "family": "plane-contact", "role": "contact-face", "dimensions": {},
            "allowedRelations": ["贴合", "重合"], "capacity": 1,
        }}],
    }


def source(identifier):
    return {"memberId": identifier, "interfaceId": "face.left"}


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {**STORAGE_ENV, "COMPONENT_DRAFT_DB": str(Path(self.directory.name) / "drafts.sqlite3")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        storage = patch('minio.Minio', return_value=MemoryMinio())
        storage.start()
        self.addCleanup(storage.stop)

    def create(self, client, **body):
        response = client.post("/api/component-drafts", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_interface_types_are_user_defined_reusable_and_synced_to_ports(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            for identifier in ("a", "b"):
                added = self.action(client, draft, {"type": "member.add", "member": member(identifier)})
                self.assertEqual(added.status_code, 200, added.text)
                draft = added.json()
            exposed = self.action(client, draft, {"type": "port.add", "port": {
                "id": "port-a", "name": "A接口", "source": source("a"), "tags": [],
            }})
            self.assertEqual(exposed.status_code, 200, exposed.text)
            draft = exposed.json()
            custom = self.action(client, draft, {"type": "interface.type.set", "memberId": "a", "interfaceId": "face.left",
                "connectionTypeId": "custom:专用支架接口", "customTypeLabel": "专用支架接口"})
            self.assertEqual(custom.status_code, 200, custom.text)
            draft = custom.json()
            self.assertEqual(draft["document"]["interfaceTypes"], [{"id": "custom:专用支架接口", "label": "专用支架接口"}])
            self.assertEqual(draft["document"]["ports"][0]["interface"]["connectionTypeId"], "custom:专用支架接口")
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.json()["stages"]["interfaceTypes"], draft["document"]["interfaceTypes"])
            reused = self.action(client, draft, {"type": "interface.type.set", "memberId": "b", "interfaceId": "face.left",
                "connectionTypeId": "custom:专用支架接口"})
            self.assertEqual(reused.status_code, 200, reused.text)
            draft = reused.json()
            cleared = self.action(client, draft, {"type": "interface.type.set", "memberId": "a", "interfaceId": "face.left", "connectionTypeId": None})
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertIsNone(cleared.json()["document"]["ports"][0]["interface"]["connectionTypeId"])
            self.assertEqual(len(cleared.json()["document"]["interfaceTypes"]), 1)
            invalid = self.action(client, cleared.json(), {"type": "interface.type.set", "memberId": "b", "interfaceId": "face.left", "connectionTypeId": "custom:missing"})
            self.assertEqual(invalid.status_code, 422)
            missing = self.action(client, cleared.json(), {"type": "interface.type.set", "memberId": "missing", "interfaceId": "face.left", "connectionTypeId": "bolt.male.v1"})
            self.assertEqual(missing.status_code, 404)

    def test_nested_component_interface_type_catalog_merge(self):
        from backend.app.components.models import ComponentAction
        from backend.app.components.service import apply_action, create_component_document

        document = create_component_document()
        document = apply_action(document, ComponentAction.validate_python({"type": "interface.types.merge", "types": [
            {"id": "custom:设备安装面", "label": "设备安装面"},
            {"id": "custom:设备安装面", "label": "设备安装面"},
        ]}))
        self.assertEqual(document.interfaceTypes[0].id, "custom:设备安装面")
        unchanged = apply_action(document, ComponentAction.validate_python({"type": "interface.types.merge", "types": [
            {"id": "custom:设备安装面", "label": "设备安装面"},
        ]}))
        self.assertEqual(unchanged.interfaceTypes, document.interfaceTypes)
        with self.assertRaises(Exception):
            apply_action(document, ComponentAction.validate_python({"type": "interface.types.merge", "types": [
                {"id": "custom:设备安装面", "label": "冲突名称"},
            ]}))

    def action(self, client, draft, action, revision=None):
        return client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
            "expectedRevision": draft["document"]["revision"] if revision is None else revision, "action": action,
        })

    def test_component_codes_are_generated_and_duplicates_fail_validation(self):
        from backend.app.components.models import ComponentAction
        from backend.app.components.repository import DraftRepository

        repository = DraftRepository(Path(self.directory.name) / "codes.sqlite3")
        first = repository.create()
        second = repository.create()
        self.assertEqual(first["document"]["definition"]["code"], "CMP-0001")
        self.assertEqual(second["document"]["definition"]["code"], "CMP-0002")
        self.assertEqual({item["code"] for item in repository.list()["items"]}, {"CMP-0001", "CMP-0002"})
        action = ComponentAction.validate_python({"type": "definition.patch", "patch": {"code": "cmp_custom-3"}})
        normalized = repository.update(second["draftId"], second["document"]["revision"], action)
        self.assertEqual(normalized["document"]["definition"]["code"], "CMP_CUSTOM-3")
        self.assertEqual(next(check for check in normalized["checks"] if check["id"] == "code.format")["status"], "pass")
        action = ComponentAction.validate_python({"type": "definition.patch", "patch": {"code": "CMP-0001"}})
        duplicate = repository.update(second["draftId"], normalized["document"]["revision"], action)
        self.assertEqual(next(check for check in duplicate["checks"] if check["id"] == "code.unique")["status"], "fail")

    def test_legacy_drafts_without_a_code_receive_one_without_changing_revision(self):
        from contextlib import closing
        import sqlite3
        from backend.app.components.repository import DraftRepository

        path = Path(self.directory.name) / "legacy.sqlite3"
        repository = DraftRepository(path)
        draft = repository.create()
        data = draft["document"]
        data["definition"]["code"] = ""
        data["revision"] = 7
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE component_drafts SET document = ?, revision = 7 WHERE id = ?",
                               (json.dumps(data, ensure_ascii=False), draft["draftId"]))

        upgraded = DraftRepository(path)
        restored = upgraded.get(draft["draftId"])
        self.assertEqual(restored["document"]["definition"]["code"], "CMP-0001")
        self.assertEqual(restored["document"]["revision"], 7)
        self.assertEqual(upgraded.list()["items"][0]["code"], "CMP-0001")

    def test_persistence_atomic_conflict_and_draft_discovery(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client, name="AI 与网页共用")
            self.assertEqual(draft["document"]["definition"]["name"], "AI 与网页共用")
            updated = self.action(client, draft, {"type": "parameter.value", "id": "componentLength", "value": 3200})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["document"]["revision"], 2)
            stale = self.action(client, draft, {"type": "parameter.value", "id": "componentLength", "value": 4000})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")
            self.assertEqual(stale.json()["error"]["current"]["document"]["parameters"][0]["value"], 3200)
            listing = client.get("/api/component-drafts").json()["items"]
            self.assertEqual(listing[0]["draftId"], draft["draftId"])
            self.assertEqual(listing[0]["revision"], 2)
        with TestClient(create_app(dist_dir=None)) as client:
            restored = client.get(f"/api/component-drafts/{draft['draftId']}")
            self.assertEqual(restored.json()["document"]["parameters"][0]["value"], 3200)
            self.assertEqual(client.get("/api/component-drafts/missing").status_code, 404)

    def test_constraint_and_dimension_based_composition_rules_persist_separately(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            self.assertFalse(any(rule.get("kind") == "composition" for rule in draft["document"]["rules"]))
            constraint = self.action(client, draft, {"type": "rule.add", "rule": {
                "id": "clearance", "kind": "constraint", "label": "安装间隙", "expression": "间隙 >= 20 mm",
            }})
            self.assertEqual(constraint.status_code, 200, constraint.text)
            composition = self.action(client, constraint.json(), {"type": "rule.add", "rule": {
                "id": "width-spacing", "kind": "composition", "label": "宽度变化时保持间距", "expression": "组件总宽变化时，组件间距保持 20 mm",
            }})
            self.assertEqual(composition.status_code, 200, composition.text)
            rules = composition.json()["document"]["rules"]
            self.assertEqual([(rule["kind"], rule["label"]) for rule in rules[-2:]], [("constraint", "安装间隙"), ("composition", "宽度变化时保持间距")])
            removed = self.action(client, composition.json(), {"type": "rule.remove", "id": "clearance"})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual([rule["id"] for rule in removed.json()["document"]["rules"]], ["rule-1", "width-spacing"])

    def test_planar_movement_persists_for_parts_and_components_and_preserves_legacy_export(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            part = member("part")
            component = member("nested")
            component["template"]["templateKind"] = "component"
            for item in (part, component):
                added = self.action(client, draft, {"type": "member.add", "member": item})
                self.assertEqual(added.status_code, 200, added.text)
                draft = added.json()
            legacy_members = draft["document"]["assembly"]["members"]
            self.assertTrue(all("planarMovement" not in item for item in legacy_members))
            export_url = f"/api/component-drafts/{draft['draftId']}/export"
            self.assertTrue(all("planarMovement" not in item for item in client.get(export_url).json()["stages"]["assembly"]["members"]))
            for identifier in ("part", "nested"):
                updated = self.action(client, draft, {"type": "member.patch", "id": identifier, "patch": {"planarMovement": True}})
                self.assertEqual(updated.status_code, 200, updated.text)
                draft = updated.json()
            self.assertTrue(all(item["planarMovement"] for item in client.get(export_url).json()["stages"]["assembly"]["members"]))

        with TestClient(create_app(dist_dir=None)) as client:
            restored = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            self.assertTrue(all(item["planarMovement"] for item in restored["document"]["assembly"]["members"]))
            disabled = self.action(client, restored, {"type": "member.patch", "id": "part", "patch": {"planarMovement": False}})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertFalse(disabled.json()["document"]["assembly"]["members"][0]["planarMovement"])
            exported = client.get(export_url).json()["stages"]["assembly"]["members"]
            self.assertNotIn("planarMovement", exported[0])
            self.assertTrue(exported[1]["planarMovement"])

    def test_member_add_suggests_length_width_and_height_bindings(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            sized = member("sized")
            sized["parameterDefinitions"] = [
                {"id": "length", "label": "零件长度", "unit": "mm", "valueType": "number", "minimum": 1000, "maximum": 6000, "default": 2400},
                {"id": "width", "label": "零件宽度", "unit": "mm", "valueType": "number", "minimum": 100, "maximum": 3000, "default": 600},
                {"id": "height", "label": "零件高度", "unit": "mm", "valueType": "number", "minimum": 300, "maximum": 8000, "default": 1800},
            ]
            added = self.action(client, draft, {"type": "member.add", "member": sized})
            self.assertEqual(added.status_code, 200, added.text)
            bindings = added.json()["document"]["bindings"]
            self.assertEqual([(binding["parameterId"], binding["memberParameterId"]) for binding in bindings], [
                ("componentLength", "length"), ("componentWidth", "width"), ("componentHeight", "height"),
            ])

    def test_invalid_actions_do_not_mutate_and_pending_is_not_pass(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            for action in [
                {"type": "parameter.value", "id": "missing", "value": 1},
                {"type": "port.add", "port": {"id": "p", "name": "p", "source": source("missing"), "tags": []}},
                {"type": "member.patch", "id": "missing", "patch": {"position": [1, 2, 3]}},
                {"type": "definition.patch", "patch": {"unknown": "ignored?"}},
            ]:
                response = self.action(client, draft, action)
                self.assertIn(response.status_code, (422, 404), response.text)
            self.assertEqual(self.action(client, draft, {"type": "parameter.value", "id": "componentLength", "value": True}).status_code, 422)
            self.assertEqual(self.action(client, draft, {"type": "definition.patch", "patch": {"name": "ok"}}, revision=True).status_code, 422)
            current = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            self.assertEqual(current["document"]["revision"], 1)
            self.assertEqual([c["id"] for c in current["checks"] if c["status"] == "pending"], ["assembly.solve", "rules.execution"])
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export")
            self.assertEqual(exported.status_code, 200)
            body = exported.json()
            self.assertEqual(body["schemaVersion"], "component-1.0")
            self.assertTrue(all(not c["passed"] for c in body["stages"]["validation"] if c["status"] == "pending"))

    def test_domain_compatibility_and_association_cleanup(self):
        from backend.app.components.models import ComponentDocument, ComponentAction
        from backend.app.components.service import apply_action, serialize_component, validate_component

        with TestClient(create_app(dist_dir=None)) as client:
            data = self.create(client)["document"]
        data["assembly"]["members"] = [member("member.a"), member("member.b")]
        data["assembly"]["members"][1]["parameterDefinitions"] = [{"id": "length", "label": "长度", "unit": "mm", "valueType": "integer", "minimum": 100, "maximum": 5000, "default": 2400}]
        data["assembly"]["members"][1]["interfaces"][0].update({
            "interfaceType": "locating", "locatingType": "planeContact", "declarationMode": "featureDerived",
            "geometryRefs": ["leftFace"], "parameterRefs": ["sectionWidth"], "description": "左端连接面", "required": True,
            "region": {"mode": "rectangle", "uSpanExpression": "sectionWidth", "vSpanExpression": "sectionHeight", "countExpression": "holeCount"},
        })
        data["assembly"]["mates"] = [{"id": "ab", "from": source("member.a"), "to": source("member.b"), "relation": "贴合", "offset": 0}]
        data["ports"] = [{"id": "p", "name": "外部接口", "source": source("member.a"), "tags": []}]
        data["slots"] = [{"id": "s", "name": "槽位", "memberId": "member.a", "required": True}]
        data["bindings"] = [{"id": "b", "parameterId": "componentLength", "memberId": "member.a", "memberParameterId": "length"}]
        actions = [
            {"type": "definition.patch", "patch": {"name": "测试组件", "code": "A"}},
            {"type": "member.patch", "id": "member.b", "patch": {"position": [1, 2, 3], "parameterValues": {"length": 3000}, "planarMovement": True}},
            {"type": "parameter.value", "id": "componentLength", "value": 3500},
            {"type": "rule.expression", "id": "rule-1", "expression": "长度约束"},
            {"type": "member.remove", "id": "member.a"},
        ]
        script = """
import {componentReducer} from './src/domain/componentReducer.ts';
import {validateComponent} from './src/domain/validation.ts';
import {serializeComponent} from './src/domain/serialization.ts';
let text='';for await(const chunk of process.stdin) text+=chunk;
const {document, actions}=JSON.parse(text);
const result=actions.reduce(componentReducer,document);
console.log(JSON.stringify({document:result,checks:validateComponent(result),export:JSON.parse(serializeComponent(result))}));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps({"document": data, "actions": actions}),
                                text=True, capture_output=True, encoding="utf-8", cwd=Path(__file__).resolve().parents[2], check=True)
        expected = json.loads(result.stdout)
        document = ComponentDocument.model_validate(data)
        # Check metadata on the member before the actions deliberately remove it.
        member_export = serialize_component(document)["stages"]["assembly"]["members"][1]
        self.assertEqual(member_export["parameterDefinitions"][0]["minimum"], 100)
        self.assertEqual(member_export["interfaces"][0]["region"]["mode"], "rectangle")
        self.assertEqual(member_export["interfaces"][0]["geometryRefs"], ["leftFace"])
        self.assertEqual(member_export["interfaces"][0]["region"]["countExpression"], "holeCount")
        for action in actions:
            document = apply_action(document, ComponentAction.validate_python(action))
        def normalize_numbers(value):
            if isinstance(value, dict):
                return {key: normalize_numbers(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize_numbers(item) for item in value]
            return int(value) if isinstance(value, float) and value.is_integer() else value
        self.assertEqual(normalize_numbers(document.model_dump(by_alias=True)), expected["document"])
        self.assertEqual(validate_component(document), expected["checks"])
        self.assertEqual(serialize_component(document), expected["export"])
        member_export = expected["export"]["stages"]["assembly"]["members"][0]
        self.assertEqual(member_export["parameterDefinitions"][0]["minimum"], 100)
        self.assertEqual(member_export["interfaces"][0]["region"]["mode"], "rectangle")
        self.assertEqual(member_export["interfaces"][0]["geometryRefs"], ["leftFace"])
        self.assertEqual(member_export["interfaces"][0]["region"]["countExpression"], "holeCount")
        self.assertEqual(document.assembly.mates, [])
        self.assertEqual(document.ports, [])
        self.assertEqual(document.bindings, [])
        self.assertIsNone(document.slots[0].memberId)

    def test_generic_component_pattern_is_saved_validated_and_cleaned(self):
        from backend.app.components.models import ComponentAction, ComponentDocument
        from backend.app.components.service import apply_action, create_component_document, serialize_component, validate_component

        document = create_component_document()
        data = document.model_dump(by_alias=True)
        data["parameters"].extend([
            {"id": "supportCount", "label": "支撑数量", "value": 3, "unit": "个", "min": 1, "max": 8},
            {"id": "supportPitch", "label": "支撑间距", "value": 450, "unit": "mm", "min": 0, "max": 1200},
        ])
        document = ComponentDocument.model_validate(data)
        document = apply_action(document, ComponentAction.validate_python({"type": "member.add", "member": member("support")}))
        document = apply_action(document, ComponentAction.validate_python({
            "type": "pattern.add", "pattern": {
                "id": "support-array", "label": "支撑阵列", "memberIds": ["support"],
                "countParameterId": "supportCount", "spacingParameterId": "supportPitch", "axis": "z",
            },
        }))
        self.assertEqual(next(check for check in validate_component(document) if check["id"] == "patterns")["status"], "pass")
        self.assertEqual(serialize_component(document)["stages"]["patterns"][0]["spacingParameterId"], "supportPitch")
        document = apply_action(document, ComponentAction.validate_python({"type": "member.remove", "id": "support"}))
        self.assertEqual(document.patterns, [])

    def test_inherited_component_port_preserves_interface_contract(self):
        from backend.app.components.models import ComponentAction
        from backend.app.components.service import apply_action, create_component_document, serialize_component, validate_component

        document = create_component_document()
        part = member("upright")
        part["interfaces"][0].update({
            "declarationMode": "featureDerived", "interfaceType": "adjustable", "locatingType": "slotAdjustable",
            "geometryRefs": ["slotFace"], "parameterRefs": ["slotWidth"], "description": "可调长孔",
            "required": True, "region": {"mode": "rectangle", "uSpanExpression": "slotWidth", "vSpan": 12.0, "countExpression": "slotCount"},
        })
        document = apply_action(document, ComponentAction.validate_python({"type": "member.add", "member": part}))
        document = apply_action(document, ComponentAction.validate_python({
            "type": "port.add", "port": {"id": "port.adjustable", "name": "可调接口", "source": source("upright"), "tags": ["adjustable"]},
        }))

        inherited = document.ports[0].interface
        self.assertIsNotNone(inherited)
        self.assertEqual(inherited.declarationMode, "featureDerived")
        self.assertEqual(inherited.geometryRefs, ["slotFace"])
        self.assertEqual(inherited.region["uSpanExpression"], "slotWidth")
        self.assertEqual(next(check for check in validate_component(document) if check["id"] == "ports")["status"], "pass")
        exported = serialize_component(document)["stages"]["ports"][0]
        self.assertEqual(exported["interface"]["region"]["countExpression"], "slotCount")
        self.assertEqual(exported["interface"]["interfaceType"], "adjustable")
        stale_data = document.model_dump(by_alias=True)
        stale_data["ports"][0]["interface"]["name"] = "旧名称"
        stale_document = type(document).model_validate(stale_data)
        self.assertEqual(next(check for check in validate_component(stale_document) if check["id"] == "ports")["status"], "fail")

    def test_component_port_can_be_removed_and_renamed(self):
        from backend.app.components.models import ComponentAction
        from backend.app.components.service import ComponentError, apply_action, create_component_document, serialize_component

        document = apply_action(create_component_document(), ComponentAction.validate_python({"type": "member.add", "member": member("upright")}))
        document = apply_action(document, ComponentAction.validate_python({
            "type": "port.add", "port": {"id": "port.one", "name": "立柱-左端面", "source": source("upright"), "tags": []},
        }))
        document = apply_action(document, ComponentAction.validate_python({"type": "port.rename", "id": "port.one", "name": "  安装接口  "}))
        self.assertEqual(document.ports[0].name, "安装接口")
        self.assertEqual(serialize_component(document)["stages"]["ports"][0]["name"], "安装接口")

        with self.assertRaises(ComponentError) as invalid:
            apply_action(document, ComponentAction.validate_python({"type": "port.rename", "id": "port.one", "name": "   "}))
        self.assertEqual(invalid.exception.code, "INVALID_PORT_NAME")
        document = apply_action(document, ComponentAction.validate_python({"type": "port.remove", "id": "port.one"}))
        self.assertEqual(document.ports, [])

    def test_port_selection_actions_persist_and_export(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            draft = self.action(client, draft, {"type": "member.add", "member": member("upright")}).json()
            draft = self.action(client, draft, {"type": "member.add", "member": member("beam")}).json()
            first_port = {"id": "port.one", "name": "立柱-左端面", "source": source("upright"), "tags": []}
            second_port = {"id": "port.two", "name": "横梁-左端面", "source": source("beam"), "tags": []}
            draft = self.action(client, draft, {"type": "port.add", "port": first_port}).json()
            draft = self.action(client, draft, {"type": "port.add", "port": second_port}).json()
            duplicate = self.action(client, draft, {"type": "port.add", "port": {**first_port, "id": "port.duplicate"}})
            self.assertEqual(duplicate.status_code, 422)

            invalid_name = self.action(client, draft, {"type": "port.rename", "id": "port.one", "name": "   "})
            self.assertEqual(invalid_name.status_code, 422)
            self.assertEqual(invalid_name.json()["error"]["code"], "INVALID_PORT_NAME")
            draft = self.action(client, draft, {"type": "port.rename", "id": "port.one", "name": "  安装接口  "}).json()

            persisted = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export").json()
            self.assertEqual([port["name"] for port in persisted["document"]["ports"]], ["安装接口", "横梁-左端面"])
            self.assertEqual([port["name"] for port in exported["stages"]["ports"]], ["安装接口", "横梁-左端面"])
            self.assertEqual(next(check for check in draft["checks"] if check["id"] == "ports")["status"], "pass")

            removed = self.action(client, draft, {"type": "port.remove", "id": "port.one"})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual([port["id"] for port in removed.json()["document"]["ports"]], ["port.two"])
            self.assertEqual(next(check for check in removed.json()["checks"] if check["id"] == "ports")["status"], "pass")
            removed = self.action(client, removed.json(), {"type": "port.remove", "id": "port.two"})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["document"]["ports"], [])
            self.assertEqual(next(check for check in removed.json()["checks"] if check["id"] == "ports")["status"], "fail")

    def test_slot_actions_require_compatible_unique_interfaces_and_export(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            first = member("base")
            first["interfaces"][0].update({"connectionGender": "male", "interfaceSize": {"diameter": 10}, "connectionMethod": "插接"})
            second = member("candidate")
            second["interfaces"][0].update({"connectionGender": "female", "interfaceSize": {"diameter": 10}, "connectionMethod": "插接"})
            bad = member("bad")
            bad["interfaces"][0]["mating"] = {"version": 1, "family": "pin-hole", "role": "hole", "dimensions": {}, "allowedRelations": ["同轴"], "capacity": 1}
            draft = self.action(client, draft, {"type": "member.add", "member": first}).json()
            draft = self.action(client, draft, {"type": "member.add", "member": second}).json()
            draft = self.action(client, draft, {"type": "member.add", "member": bad}).json()
            slot = {"id": "slot.one", "name": "安装位", "required": True, "baseInterface": None, "memberId": None, "interfaceId": None}
            draft = self.action(client, draft, {"type": "slot.add", "slot": slot}).json()
            draft = self.action(client, draft, {"type": "slot.patch", "id": "slot.one", "patch": {"required": False}}).json()
            self.assertFalse(draft["document"]["slots"][0]["required"])
            draft = self.action(client, draft, {"type": "slot.patch", "id": "slot.one", "patch": {"required": True}}).json()
            source_base = source("base")
            draft = self.action(client, draft, {"type": "slot.patch", "id": "slot.one", "patch": {"baseInterface": source_base}}).json()
            incompatible = self.action(client, draft, {"type": "slot.patch", "id": "slot.one", "patch": {"memberId": "bad", "interfaceId": "face.left"}})
            self.assertEqual(incompatible.status_code, 422)
            self.assertEqual(incompatible.json()["error"]["code"], "INCOMPATIBLE_SLOT_INTERFACE")
            draft = self.action(client, draft, {"type": "slot.patch", "id": "slot.one", "patch": {"memberId": "candidate", "interfaceId": "face.left"}}).json()
            self.assertEqual(draft["document"]["slots"][0]["interfaceId"], "face.left")
            persisted = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export").json()
            self.assertEqual(persisted["document"]["slots"][0]["baseInterface"], source_base)
            self.assertEqual(exported["stages"]["slots"][0]["interfaceId"], "face.left")
            duplicate = self.action(client, draft, {"type": "slot.add", "slot": {**slot, "id": "slot.two", "baseInterface": source_base}}).json()
            duplicate_assignment = self.action(client, duplicate, {"type": "slot.patch", "id": "slot.two", "patch": {"memberId": "candidate", "interfaceId": "face.left"}})
            self.assertEqual(duplicate_assignment.status_code, 422)
            self.assertEqual(duplicate_assignment.json()["error"]["code"], "DUPLICATE_SLOT_INTERFACE")
            removed = self.action(client, duplicate, {"type": "member.remove", "id": "base"}).json()
            self.assertIsNone(removed["document"]["slots"][0]["baseInterface"])

    def test_clearing_component_parameter_removes_member_override(self):
        from backend.app.components.models import ComponentAction
        from backend.app.components.service import apply_action, create_component_document

        part = member("upright")
        part["parameterDefinitions"] = [{"id": "length", "label": "零件长度", "unit": "mm", "valueType": "number", "minimum": 1800.0, "maximum": 4200.0, "default": 2400.0}]
        document = apply_action(create_component_document(), ComponentAction.validate_python({"type": "member.add", "member": part}))
        document = apply_action(document, ComponentAction.validate_python({"type": "binding.set", "id": "length.binding", "binding": {"id": "length.binding", "parameterId": "componentLength", "memberId": "upright", "memberParameterId": "length"}}))
        document = apply_action(document, ComponentAction.validate_python({"type": "parameter.value", "id": "componentLength", "value": 3600.0}))
        self.assertEqual(document.assembly.members[0].parameterValues["length"], 3600.0)
        document = apply_action(document, ComponentAction.validate_python({"type": "parameter.value", "id": "componentLength", "value": None}))
        self.assertIsNone(document.parameters[0].value)
        self.assertNotIn("length", document.assembly.members[0].parameterValues)

    def test_nested_import_validation_and_noop_revision(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            unchanged = self.action(client, draft, {"type": "parameter.value", "id": "componentLength", "value": 2400})
            self.assertEqual(unchanged.status_code, 200, unchanged.text)
            self.assertEqual(unchanged.json()["document"]["revision"], 1)
            data = draft["document"]
            data["assembly"]["members"] = [member("member.a"), member("member.b")]
            imported = self.create(client, document=data)
            added = self.action(client, imported, {"type": "mate.add", "mate": {
                "id": "ab", "from": source("member.a"), "to": source("member.b"), "relation": "贴合", "offset": 0,
            }})
            self.assertEqual(added.status_code, 200, added.text)
            self.assertEqual(added.json()["document"]["assembly"]["mates"][0]["from"], source("member.a"))
            invalid = json.loads(json.dumps(data))
            invalid["assembly"]["members"][0]["template"]["secret"] = "must not persist"
            self.assertEqual(client.post("/api/component-drafts", json={"document": invalid}).status_code, 422)
            invalid = json.loads(json.dumps(data))
            invalid["assembly"]["members"][0]["position"][0] = True
            self.assertEqual(client.post("/api/component-drafts", json={"document": invalid}).status_code, 422)
            self.assertEqual(self.action(client, imported, {"type": "definition.patch", "patch": {"name": None}}).status_code, 422)

    def test_mate_service_rejects_unassessed_contracts_and_consumed_endpoints(self):
        with TestClient(create_app(dist_dir=None)) as client:
            data = self.create(client)["document"]
            data["assembly"]["members"] = [member("a"), member("b"), member("c")]
            data["assembly"]["members"][2]["interfaces"][0].pop("mating")
            draft = self.create(client, document=data)
            valid_mate = {"id": "ab", "from": source("a"), "to": source("b"), "relation": "贴合", "offset": 0}
            added = self.action(client, draft, {"type": "mate.add", "mate": valid_mate})
            self.assertEqual(added.status_code, 200, added.text)
            consumed = self.action(client, added.json(), {"type": "mate.add", "mate": {
                "id": "ac", "from": source("a"), "to": source("c"), "relation": "贴合", "offset": 0,
            }})
            self.assertEqual(consumed.status_code, 422)
            self.assertEqual(consumed.json()["error"]["code"], "INTERFACE_REQUIRES_REVIEW")
            self.assertEqual(consumed.json()["error"]["current"][0]["code"], "MISSING_CONTRACT")

            fresh = self.create(client, document={**data, "assembly": {"members": data["assembly"]["members"][:2], "mates": []}})
            first = self.action(client, fresh, {"type": "mate.add", "mate": valid_mate})
            self.assertEqual(first.status_code, 200, first.text)
            third = member("third")
            appended = self.action(client, first.json(), {"type": "member.add", "member": third})
            self.assertEqual(appended.status_code, 200, appended.text)
            over_capacity = self.action(client, appended.json(), {"type": "mate.add", "mate": {
                "id": "ac", "from": source("a"), "to": source("third"), "relation": "贴合", "offset": 0,
            }})
            self.assertEqual(over_capacity.status_code, 422)
            self.assertEqual(over_capacity.json()["error"]["code"], "ENDPOINT_ALREADY_CONSUMED")

    def test_definition_metadata_and_portable_evidence_export(self):
        import base64
        from uuid import uuid4

        content = b"evidence-image-content"
        evidence_id = str(uuid4())
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            upload = client.put(
                f"/api/component-drafts/{draft['draftId']}/evidence/{evidence_id}?fileName=%E8%BF%9E%E6%8E%A5%E7%A4%BA%E6%84%8F.png",
                content=content, headers={"Content-Type": "image/png"},
            )
            self.assertEqual(upload.status_code, 200, upload.text)
            reference = upload.json()
            patched = self.action(client, draft, {"type": "definition.patch", "patch": {
                "projectCode": "RACK-2026-001", "owner": "张工", "organization": "结构组",
                "searchTags": ["货架", "横梁"], "partConnectionType": "自定义",
                "partConnectionCustom": "端板螺栓连接", "componentConnectionType": "卡扣连接",
                "designConstraints": "端部需避开立柱加强筋", "evidenceFiles": [reference],
            }})
            self.assertEqual(patched.status_code, 200, patched.text)
            definition = patched.json()["document"]["definition"]
            self.assertEqual(definition["projectCode"], "RACK-2026-001")
            self.assertRegex(definition["code"], r"^CMP-\d{4,}$")
            self.assertEqual(definition["code"], draft["document"]["definition"]["code"])
            self.assertEqual(definition["owner"], "张工")
            self.assertEqual(definition["componentConnectionType"], "卡扣连接")
            self.assertIsNone(definition["evidenceFiles"][0]["dataUrl"])
            downloaded = client.get(f"/api/component-drafts/{draft['draftId']}/evidence/{evidence_id}")
            self.assertEqual(downloaded.content, content)
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            portable = exported.json()["stages"]["definition"]["evidenceFiles"][0]
            self.assertEqual(portable["dataUrl"], f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}")

            import_document = patched.json()["document"]
            import_document["definition"]["evidenceFiles"] = [portable]
            restored = self.create(client, document=import_document)
            roundtrip = client.get(f"/api/component-drafts/{restored['draftId']}/evidence/{evidence_id}")
            self.assertEqual(roundtrip.content, content)

            removed = self.action(client, patched.json(), {"type": "definition.patch", "patch": {"evidenceFiles": []}})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(client.get(f"/api/component-drafts/{draft['draftId']}/evidence/{evidence_id}").status_code, 404)

    def test_two_repository_instances_cannot_both_commit_same_revision(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from backend.app.components.models import ComponentAction
        from backend.app.components.repository import DraftRepository
        from backend.app.components.service import ComponentError

        path = Path(self.directory.name) / "race.sqlite3"
        first, second = DraftRepository(path), DraftRepository(path)
        draft = first.create()
        barrier = Barrier(2)

        def edit(repository, value):
            barrier.wait()
            try:
                return repository.update(draft["draftId"], 1, ComponentAction.validate_python({
                    "type": "parameter.value", "id": "componentLength", "value": value,
                }))
            except ComponentError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit, first, 3100), pool.submit(edit, second, 3200)]
            results = [future.result() for future in futures]
        errors = [result for result in results if isinstance(result, ComponentError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "REVISION_CONFLICT")
        self.assertEqual(first.get(draft["draftId"])["document"]["revision"], 2)

    def test_publish_snapshot_reuse_and_republish_as_immutable_versions(self):
        with TestClient(create_app(dist_dir=None)) as client:
            first = self.create(client, name="底座组件")
            first_publish = client.post(f"/api/component-drafts/{first['draftId']}/publish", json={"expectedRevision": 1})
            self.assertEqual(first_publish.status_code, 200, first_publish.text)
            published = first_publish.json()
            self.assertEqual(published["version"], 1)
            self.assertEqual(published["checks"][-2]["status"], "pending")
            retry = client.post(f"/api/component-drafts/{first['draftId']}/publish", json={"expectedRevision": 1})
            self.assertEqual(retry.json(), published)
            self.assertEqual(client.get("/api/published-components").json()["count"], 1)
            package = client.get(published["downloadUrl"])
            self.assertEqual(hashlib.sha256(package.content).hexdigest(), published["sha256"])
            self.assertEqual(len(package.content), published["size"])
            with ZipFile(BytesIO(package.content)) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "component.json", "checks.json"})
                self.assertEqual(json.loads(archive.read("component.json")), published["document"])
                self.assertEqual(json.loads(archive.read("checks.json")), published["checks"])

            second = self.create(client, name="设备组件")
            component_member = member("base.instance")
            component_member["template"] = {
                "templateKind": "component", "publicationId": published["publicationId"],
                "templateId": published["templateId"], "version": published["version"], "sha256": published["sha256"],
            }
            component_member["interfaces"] = []
            added = self.action(client, second, {"type": "member.add", "member": component_member})
            self.assertEqual(added.status_code, 200, added.text)
            second_publish = client.post(f"/api/component-drafts/{second['draftId']}/publish", json={"expectedRevision": 2})
            self.assertEqual(second_publish.status_code, 200, second_publish.text)
            self.assertEqual(second_publish.json()["version"], 1)
            self.assertEqual(second_publish.json()["document"]["assembly"]["members"][0]["template"]["publicationId"], published["publicationId"])

            client.post(f"/api/component-drafts/{first['draftId']}/actions", json={
                "expectedRevision": 1, "action": {"type": "definition.patch", "patch": {"name": "修改后的底座"}},
            })
            second_version = client.post(f"/api/component-drafts/{first['draftId']}/publish", json={"expectedRevision": 2}).json()
            self.assertEqual(second_version["version"], 2)
            detail = client.get(f"/api/published-components/{published['publicationId']}").json()
            self.assertEqual(detail["document"]["definition"]["name"], "底座组件")
            self.assertEqual(client.get("/api/published-components").json()["count"], 3)
        with TestClient(create_app(dist_dir=None)) as client:
            self.assertEqual(client.get("/api/published-components").json()["count"], 3)
            self.assertEqual(client.get(f"/api/published-components/{published['publicationId']}").json(), published)

    def test_publish_rejects_nonexistent_or_mismatched_component_dependencies(self):
        with TestClient(create_app(dist_dir=None)) as client:
            original = self.create(client)
            published = client.post(f"/api/component-drafts/{original['draftId']}/publish", json={"expectedRevision": 1}).json()
            for reference, expected_status in [
                ({"publicationId": "nonexistent"}, 404),
                ({"sha256": "b" * 64}, 422),
                ({"version": 2}, 422),
                ({"templateId": "wrong-template"}, 422),
            ]:
                document = original["document"]
                nested = member("nested")
                nested["template"] = {"templateKind": "component", **{key: published[key] for key in ("publicationId", "templateId", "version", "sha256")}, **reference}
                document["assembly"]["members"] = [nested]
                draft = self.create(client, document=document)
                result = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={"expectedRevision": 1})
                self.assertEqual(result.status_code, expected_status, result.text)
            self.assertEqual(client.get("/api/published-components").json()["count"], 1)

    def test_publish_requires_current_saved_revision(self):
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
                "expectedRevision": 1, "action": {"type": "definition.patch", "patch": {"name": "新名称"}},
            })
            response = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={"expectedRevision": 1})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
