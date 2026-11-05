"""Cross-component contracts, evidence freshness and shared AI/human boundaries."""
from copy import deepcopy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.components.models import ComponentAction, ComponentDocument, PortSpecification, PortTrialOptions
from backend.app.components.repository import DraftRepository
from backend.app.components.service import ComponentError, create_component_document
from backend.app.components.ports import match_rules, port_diagnostics, trial_ports
from backend.tests.test_components import member

def calibrated(identifier, position=None):
    result = member(identifier)
    result["position"] = position or [0, 0, 0]
    result["interfaces"][0]["frame"] = {"origin": [0, 0, 0], "rotation": [0, 0, 0]}
    return result


def document(role="male", value=10, unit="mm"):
    data = create_component_document().model_dump(by_alias=True)
    member = calibrated("part", [100, 20, 30])
    member["rotation"] = [.2, -.4, .6]
    member["interfaces"][0]["frame"] = {"origin": [5, 7, -2], "rotation": [-.1, .3, -.2]}
    other = calibrated("second", [-50, 0, 100])
    data["assembly"]["members"] = [member, other]
    own, partner = ("pin", "socket") if role == "male" else ("socket", "pin")
    data["ports"] = [{"id": "p", "name": "test", "source": {"memberId": "part", "interfaceId": "face.left"},
        "tags": [own], "compatibleTags": [partner], "allowedRelations": ["固定"], "specification": {
            "category": "test-pin-v1", "role": role, "contact": "clearance", "clearanceMinMm": .1, "clearanceMaxMm": 1,
            "dimensions": [{"key": "diameter", "value": value, "unit": unit, "tolerance": .01 if unit == "mm" else .001}]}}]
    return ComponentDocument.model_validate(data)


class PortWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = DraftRepository(Path(self.temporary.name) / "drafts.sqlite3")
        self.a = self.repo.create(document=document())
        self.b = self.repo.create(document=document("female", 1.05, "cm"))
        self.confirm(self.a)
        self.confirm(self.b)

    def edit(self, draft, action, actor="user", reason="test evidence"):
        result = self.repo.update(draft["draftId"], draft["document"]["revision"], ComponentAction.validate_python(action), actor=actor, reason=reason)
        draft.clear(); draft.update(result)
        return result

    def confirm(self, draft):
        return self.edit(draft, {"type": "port.confirm", "id": "p", "scope": "basis", "note": "人工核对连接点、X朝向和Z法向"})

    def options(self, **fields):
        return PortTrialOptions(targetDraftId=self.b["draftId"], targetPortId="p", expectedTargetRevision=self.b["document"]["revision"], **fields)

    def record(self):
        return self.edit(self.a, {"type": "port.trial.record", "id": "p", "options": self.options().model_dump()}, actor="agent", reason="比较直径与公差，并用固定连接预览接口")

    def test_readonly_trial_moves_whole_component_and_preserves_internal_distance(self):
        before = deepcopy(self.repo.get(self.a["draftId"])["document"])
        result = self.repo.preview_ports(self.a["draftId"], before["revision"], "p", self.options(offset=2, angle=.4))
        self.assertEqual(result["report"]["rules"]["status"], "pass")
        self.assertEqual(result["report"]["alignment"]["status"], "pass")
        self.assertEqual(result["report"]["collision"]["status"], "pending")
        self.assertEqual(result["report"]["status"], "pending")
        row = result["report"]["rules"]["dimensions"][0]
        self.assertAlmostEqual(row["clearanceMm"], .5)
        self.assertAlmostEqual(row["uncertaintyMm"], .02)
        old = before["assembly"]["members"]
        new = result["movingMembers"]
        self.assertAlmostEqual(math.dist(old[0]["position"], old[1]["position"]), math.dist(new[0]["position"], new[1]["position"]))
        self.assertNotEqual(new[0]["position"], old[0]["position"])
        self.assertEqual(self.repo.get(self.a["draftId"])["document"], before)
        self.assertEqual(self.repo.get(self.b["draftId"])["document"], self.b["document"])

    def test_saved_report_human_review_export_and_revision_bound_audit(self):
        assembly = deepcopy(self.a["document"]["assembly"])
        self.record()
        d = self.a["portDiagnostics"][0]
        self.assertTrue(d["trialCurrent"])
        self.assertEqual(d["layers"]["rules"], "pass")
        self.assertEqual(d["layers"]["geometry"], "alignment-pass-collision-pending")
        self.assertEqual(self.a["document"]["assembly"], assembly)
        event = self.a["operations"][0]
        self.assertEqual(event["actor"], "agent")
        self.assertIn("比较直径", event["reason"])
        self.assertEqual(event["beforeRevision"] + 1, event["revision"])
        self.assertTrue(any(item["path"][:2] == ["ports", "p"] for item in event["changes"]))
        self.edit(self.a, {"type": "port.confirm", "id": "p", "scope": "review", "note": "规则通过，人工查看预览；待实体碰撞验证"})
        self.assertEqual(self.a["portDiagnostics"][0]["layers"]["review"], "pass")
        self.edit(self.b, {"type": "definition.patch", "patch": {"name": "changed target revision"}})
        current = self.repo.get(self.a["draftId"])["portDiagnostics"][0]
        self.assertFalse(current["trialCurrent"])
        self.assertEqual(current["layers"]["review"], "stale")
        self.assertTrue(any("目标" in reason for reason in current["reviewReasons"]))

    def test_source_parameter_pose_and_frame_changes_expire_evidence_and_explicit_restore(self):
        self.record()
        original = deepcopy(self.a["document"]["assembly"]["members"][0])
        for patch in ({"parameterValues": {"length": 42}}, {"position": [1, 2, 3]}, {"rotation": [0, 0, math.pi/2]}):
            self.edit(self.a, {"type": "member.patch", "id": "part", "patch": patch})
            self.assertEqual(self.a["portDiagnostics"][0]["layers"]["basis"], "stale")
            self.assertFalse(self.a["portDiagnostics"][0]["trialCurrent"])
            self.assertTrue(self.a["portDiagnostics"][0]["sourceChanges"])
            self.edit(self.a, {"type": "member.patch", "id": "part", "patch": {key: original[key] for key in patch}})
            self.assertTrue(self.a["portDiagnostics"][0]["trialCurrent"])
        self.edit(self.a, {"type": "interface.set", "memberId": "part", "interfaceId": "face.left", "frame": {"origin": [9, 0, 0], "rotation": [0, 0, 0]}})
        self.assertIn("接口基准/类型", self.a["portDiagnostics"][0]["sourceChanges"])

    def test_source_version_update_and_lost_target_are_detected(self):
        from backend.app.components.ports import digest, source_context
        a = ComponentDocument.model_validate(self.a["document"])
        old = a.ports[0].basisConfirmation.key
        a.assembly.members[0].template.version += 1
        self.assertNotEqual(old, digest(source_context(a, a.ports[0])))
        self.assertEqual(port_diagnostics(a)[0]["layers"]["basis"], "stale")
        self.record()
        self.edit(self.b, {"type": "port.remove", "id": "p"})
        d = self.repo.get(self.a["draftId"])["portDiagnostics"][0]
        self.assertFalse(d["trialCurrent"])
        self.assertIn("目标草稿或对外接口已失效", d["reviewReasons"])

    def test_ai_cannot_claim_human_confirmation_and_failure_is_logged(self):
        before = deepcopy(self.a["document"])
        for scope in ("basis", "review"):
            with self.assertRaises(ComponentError) as caught:
                self.edit(self.a, {"type": "port.confirm", "id": "p", "scope": scope, "note": "agent says yes"}, actor="agent")
            self.assertEqual(caught.exception.code, "HUMAN_CONFIRMATION_REQUIRED")
        current = self.repo.get(self.a["draftId"])
        self.assertEqual(current["document"], before)
        self.assertEqual(current["operations"][0]["status"], "error")
        self.assertEqual(current["operations"][0]["error"]["code"], "HUMAN_CONFIRMATION_REQUIRED")

    def test_strict_target_revision_and_self_trial_rejected_without_changes(self):
        before = deepcopy(self.a["document"])
        stale = self.options()
        self.edit(self.b, {"type": "definition.patch", "patch": {"name": "new"}})
        with self.assertRaises(ComponentError) as caught:
            self.repo.preview_ports(self.a["draftId"], before["revision"], "p", stale)
        self.assertEqual(caught.exception.code, "TARGET_REVISION_CONFLICT")
        with self.assertRaises(ComponentError) as caught:
            self.repo.preview_ports(self.a["draftId"], before["revision"], "p", PortTrialOptions(targetDraftId=self.a["draftId"], targetPortId="p", expectedTargetRevision=before["revision"]))
        self.assertEqual(caught.exception.code, "SELF_TRIAL")
        self.assertEqual(self.repo.get(self.a["draftId"])["document"], before)

    def test_ai_import_and_add_cannot_supply_human_evidence(self):
        before_count = len(self.repo.list()["items"])
        with self.assertRaises(ComponentError) as caught:
            self.repo.create(document=ComponentDocument.model_validate(self.a["document"]), actor="agent")
        self.assertEqual(caught.exception.code, "HUMAN_CONFIRMATION_REQUIRED")
        self.assertEqual(len(self.repo.list()["items"]), before_count)
        port = deepcopy(self.a["document"]["ports"][0])
        port["id"] = "forged"
        with self.assertRaises(ComponentError) as caught:
            self.edit(self.a, {"type": "port.add", "port": port}, actor="agent")
        self.assertEqual(caught.exception.code, "SERVER_EVIDENCE_ONLY")

    def test_unknown_tolerances_roles_and_tags_never_count_as_match(self):
        a, b = ComponentDocument.model_validate(self.a["document"]), ComponentDocument.model_validate(self.b["document"])
        a.ports[0].specification.dimensions[0].tolerance = None
        result = match_rules(a, a.ports[0], b, b.ports[0], "固定")
        self.assertEqual(result["status"], "pending")
        self.assertTrue(any(item["code"] == "TOLERANCE_UNKNOWN" for item in result["issues"]))
        b.ports[0].specification.role = "male"
        b.ports[0].tags = []
        result = match_rules(a, a.ports[0], b, b.ports[0], "固定")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(item["code"] == "ROLE_MISMATCH" for item in result["issues"]))
        self.assertTrue(any(item["code"] == "TARGET_TAG_MISMATCH" for item in result["issues"]))

    def test_signed_pressfit_and_tolerance_extremes_checked(self):
        a, b = ComponentDocument.model_validate(self.a["document"]), ComponentDocument.model_validate(self.b["document"])
        b.ports[0].specification.dimensions[0].value = .98
        for p in (a.ports[0], b.ports[0]):
            p.specification.contact = "press-fit"
            p.specification.clearanceMinMm = -.25
            p.specification.clearanceMaxMm = -.15
        result = match_rules(a, a.ports[0], b, b.ports[0], "固定")
        # Basis snapshots deliberately expire when source changes, but contract edits do not affect their coordinates.
        self.assertEqual(result["status"], "pass")
        self.assertAlmostEqual(result["dimensions"][0]["clearanceMm"], -.2)
        a.ports[0].specification.dimensions[0].tolerance = .1
        result = match_rules(a, a.ports[0], b, b.ports[0], "固定")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(item["code"] == "CLEARANCE_OUT_OF_RANGE" for item in result["issues"]))

    def test_schema_rejects_duplicate_dimensions_bad_ranges_and_evidence_in_patch(self):
        from pydantic import ValidationError
        spec = self.a["document"]["ports"][0]["specification"]
        for fields in ({"dimensions": spec["dimensions"] * 2}, {"clearanceMinMm": 10, "clearanceMaxMm": 1}, {"dimensions": [{"key": "d", "value": float("inf"), "unit": "mm"}]}, {"dimensions": [{"key": "d", "value": -1, "unit": "mm"}]}):
            with self.assertRaises(ValidationError): PortSpecification.model_validate({**spec, **fields})
        with self.assertRaises(ValidationError): ComponentAction.validate_python({"type": "port.patch", "id": "p", "patch": {"basisConfirmation": self.a["document"]["ports"][0]["basisConfirmation"]}})
        normalized = PortSpecification.model_validate({**spec, "dimensions": [{**spec["dimensions"][0], "key": " diameter "}]})
        self.assertEqual(normalized.dimensions[0].key, "diameter")
        with self.assertRaises(ValidationError):
            PortSpecification.model_validate({**spec, "dimensions": [{**spec["dimensions"][0], "key": key} for key in ("diameter", " diameter ")]})

    def test_rest_and_mcp_use_same_report_and_export_layers(self):
        with TestClient(create_app(dist_dir=None, draft_db=self.repo.path), base_url="http://127.0.0.1:8020", client=("127.0.0.1", 12345)) as client:
            path = f'/api/component-drafts/{self.a["draftId"]}'
            body = {"expectedRevision": self.a["document"]["revision"], "portId": "p", "options": self.options().model_dump()}
            rest = client.post(path + "/ports/trial", json=body)
            self.assertEqual(rest.status_code, 200, rest.text)
            rpc = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "component_trial_ports", "arguments": {"draftId": self.a["draftId"], **body}}}).json()["result"]
            self.assertFalse(rpc.get("isError"), rpc)
            self.assertEqual(rest.json(), rpc["structuredContent"])
            response = client.post(path + "/actions", json={"expectedRevision": self.a["document"]["revision"], "action": {"type": "port.trial.record", "id": "p", "options": self.options().model_dump()}, "reason": "网页记录检查依据"})
            self.assertEqual(response.status_code, 200, response.text)
            current = response.json()
            self.assertTrue(current["portDiagnostics"][0]["trialCurrent"])
            self.assertEqual(client.get(path + "/export").json()["stages"]["portDiagnostics"], current["portDiagnostics"])

    def test_import_rejects_malformed_or_claimed_collision_report(self):
        from pydantic import ValidationError
        self.record()
        data = deepcopy(self.a["document"])
        data["ports"][0]["trial"]["report"] = {"rules": {"status": "pass"}}
        with self.assertRaises(ValidationError): ComponentDocument.model_validate(data)
        data = deepcopy(self.a["document"])
        data["ports"][0]["trial"]["report"]["collision"]["status"] = "pass"
        with self.assertRaises(ValidationError): ComponentDocument.model_validate(data)

    def test_port_crud_and_calibration_preserve_publication_snapshot_and_assembly(self):
        before = deepcopy(self.a["document"])
        self.edit(self.a, {"type": "port.patch", "id": "p", "patch": {
            "name": "renamed", "source": {"memberId": "second", "interfaceId": "face.left"}}})
        port = self.a["document"]["ports"][0]
        self.assertEqual(port["id"], "p")
        self.assertEqual(port["interface"], self.a["document"]["assembly"]["members"][1]["interfaces"][0])
        self.edit(self.a, {"type": "interface.set", "memberId": "second", "interfaceId": "face.left",
            "frame": {"origin": [1, 2, 3], "rotation": [0, 0, .5]}})
        current = self.a["document"]
        self.assertEqual(current["ports"][0]["interface"], current["assembly"]["members"][1]["interfaces"][0])
        for old, new in zip(before["assembly"]["members"], current["assembly"]["members"]):
            self.assertEqual(old["template"], new["template"])
            self.assertEqual(old["position"], new["position"])
            self.assertEqual(old["rotation"], new["rotation"])
        published = self.repo.prepare_publication(self.a["draftId"], current["revision"])
        self.assertEqual(published["document"]["ports"], current["ports"])
        self.assertEqual(published["document"]["definition"], current["definition"])
        assembly = deepcopy(current["assembly"])
        self.edit(self.a, {"type": "port.remove", "id": "p"})
        self.assertEqual(self.a["document"]["ports"], [])
        self.assertEqual(self.a["document"]["assembly"], assembly)
        self.assertEqual(self.repo.publication(published["publicationId"])["document"]["ports"], current["ports"])

    def test_mcp_port_command_uses_shared_contract_and_refuses_human_confirmation(self):
        with TestClient(create_app(dist_dir=None, draft_db=self.repo.path), base_url="http://127.0.0.1:8020", client=("127.0.0.1", 12345)) as client:
            def command(action):
                revision = self.repo.get(self.a["draftId"])["document"]["revision"]
                return client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "component_execute_action", "arguments": {"draftId": self.a["draftId"],
                            "expectedRevision": revision, "action": action, "reason": "test AI contract"}}}).json()["result"]
            result = command({"type": "port.patch", "id": "p", "patch": {"name": "AI changed"}})
            self.assertFalse(result.get("isError"), result)
            self.assertEqual(self.repo.get(self.a["draftId"])["document"]["ports"][0]["name"], "AI changed")
            result = command({"type": "port.confirm", "id": "p", "scope": "basis", "note": "forbidden"})
            self.assertTrue(result["isError"])
            self.assertIn("HUMAN_CONFIRMATION_REQUIRED", str(result))


if __name__ == "__main__":
    unittest.main()
