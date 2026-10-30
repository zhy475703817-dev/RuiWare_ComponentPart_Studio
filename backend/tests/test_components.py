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
        "interfaces": [{"id": "face.left", "name": "左端面", "type": "plane", "tags": ["end-face"]}],
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

    def action(self, client, draft, action, revision=None):
        return client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
            "expectedRevision": draft["document"]["revision"] if revision is None else revision, "action": action,
        })

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
        data["assembly"]["members"][0]["parameterDefinitions"] = [{"id": "length", "label": "长度", "unit": "mm", "valueType": "integer", "minimum": 100, "maximum": 5000, "default": 2400}]
        data["assembly"]["members"][0]["interfaces"][0].update({
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
            {"type": "member.patch", "id": "member.b", "patch": {"position": [1, 2, 3], "parameterValues": {"length": 3000}}},
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
        for action in actions:
            document = apply_action(document, ComponentAction.validate_python(action))
        self.assertEqual(document.model_dump(by_alias=True), expected["document"])
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

    def test_definition_extensions_persist_and_export(self):
        patch = {
            "projectCode": "RACK-2026-01", "owner": "张工", "organization": "研发部",
            "searchTags": ["横梁", "可调"],
            "connectionForms": {
                "internal": {"mode": "preset", "value": "螺栓连接", "details": "M8 紧固"},
                "interComponent": {"mode": "custom", "value": "挂钩插接", "details": "免工具拆装"},
            },
            "designConstraints": "跨度不超过 2400 mm", "evidence": [{
                "id": "e1", "name": "安装参考.png", "mediaType": "image/png", "size": 1024,
                "addedAt": "2026-10-28T00:00:00.000Z",
            }],
        }
        with TestClient(create_app(dist_dir=None)) as client:
            draft = self.create(client)
            updated = self.action(client, draft, {"type": "definition.patch", "patch": patch})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["document"]["definition"]["projectCode"], "RACK-2026-01")
            persisted = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            self.assertEqual(persisted["document"]["definition"]["connectionForms"]["interComponent"]["mode"], "custom")
            self.assertEqual(persisted["document"]["definition"]["evidence"][0]["name"], "安装参考.png")
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export").json()
            self.assertEqual(exported["stages"]["definition"]["searchTags"], ["横梁", "可调"])
            self.assertEqual(exported["stages"]["definition"]["designConstraints"], "跨度不超过 2400 mm")

    def test_evidence_upload_is_referenced_and_cleaned_up(self):
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
            reference = {"id": evidence_id, "name": "连接示意.png", "mediaType": "image/png",
                         "size": len(content), "addedAt": "2026-10-28T00:00:00.000Z"}
            patched = self.action(client, draft, {"type": "definition.patch", "patch": {"evidence": [reference]}})
            self.assertEqual(patched.status_code, 200, patched.text)
            downloaded = client.get(f"/api/component-drafts/{draft['draftId']}/evidence/{evidence_id}")
            self.assertEqual(downloaded.content, content)
            exported = client.get(f"/api/component-drafts/{draft['draftId']}/export").json()
            self.assertEqual(
                exported["stages"]["definition"]["evidence"][0]["dataUrl"],
                f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}",
            )
            removed = self.action(client, patched.json(), {"type": "definition.patch", "patch": {"evidence": []}})
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
