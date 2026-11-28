import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class ActionBatchTests(unittest.TestCase):
    def test_batch_commits_one_revision_and_rolls_back_invalid_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(draft_db=Path(directory) / "draft.sqlite"), dist_dir=None)
            with TestClient(app) as client:
                app.state.qualification_jobs.debounce = 100
                draft = client.post("/api/component-drafts", json={}).json()
                base = "/api/component-drafts/" + draft["draftId"]
                response = client.post(base + "/actions/batch", json={"expectedRevision": 1, "actions": [
                    {"type": "definition.patch", "patch": {"name": "批量名称"}},
                    {"type": "definition.patch", "patch": {"owner": "负责人"}},
                ]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["document"]["revision"], 2)
                response = client.post(base + "/actions/batch", json={"expectedRevision": 2, "actions": [
                    {"type": "definition.patch", "patch": {"name": "应回滚"}},
                    {"type": "member.patch", "id": "missing", "patch": {"position": [50, 0, 0]}},
                ]})
                self.assertEqual(response.status_code, 404, response.text)
                current = client.get(base).json()["document"]
                self.assertEqual(current["revision"], 2)
                self.assertEqual(current["definition"]["name"], "批量名称")
                response = client.post(base + "/actions/batch", json={"expectedRevision": 1, "actions": [
                    {"type": "definition.patch", "patch": {"name": "旧修订"}},
                ]})
                self.assertEqual(response.status_code, 409)
                for actions in ([], [{"type": "reset"}] * 101):
                    response = client.post(base + "/actions/batch", json={"expectedRevision": 2, "actions": actions})
                    self.assertEqual(response.status_code, 422)

    def test_stale_model_snapshot_cannot_erase_agent_model_edits(self):
        from backend.app.components.models import ComponentAction
        from backend.tests.test_components import member
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(draft_db=Path(directory) / "draft.sqlite"), dist_dir=None)
            with TestClient(app) as client:
                app.state.qualification_jobs.debounce = 100
                draft = client.post("/api/component-drafts", json={}).json()
                document = draft["document"]
                model = {key: document[key] for key in ("assembly", "ports", "bindings", "patterns", "slots", "braceGenerationRules")}
                app.state.drafts.update(draft["draftId"], 1, ComponentAction.validate_python({"type": "member.add", "member": member("agent-member")}), actor="agent")
                base = "/api/component-drafts/" + draft["draftId"]
                response = client.post(base + "/actions/batch", json={"expectedRevision": 1, "actions": [{"type": "model.restore", "model": model}]})
                self.assertEqual(response.status_code, 409, response.text)
                current = client.get(base).json()["document"]
                self.assertEqual(current["assembly"]["members"][0]["id"], "agent-member")
