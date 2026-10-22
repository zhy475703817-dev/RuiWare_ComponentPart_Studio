import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app


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
        self.environment = patch.dict(os.environ, {"COMPONENT_DRAFT_DB": str(Path(self.directory.name) / "drafts.sqlite3")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

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
        self.assertEqual(document.assembly.mates, [])
        self.assertEqual(document.ports, [])
        self.assertEqual(document.bindings, [])
        self.assertIsNone(document.slots[0].memberId)

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
            self.assertEqual(definition["code"], "")
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


if __name__ == "__main__":
    unittest.main()
