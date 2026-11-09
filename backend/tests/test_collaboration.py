from pathlib import Path
from contextlib import closing
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from backend.app.components.repository import DraftRepository
from backend.app.components.models import ComponentDocument
from backend.app.components.service import ComponentError
from backend.app.main import create_app
from backend.tests.test_components import member


def definition(**patch):
    return {"type": "definition.patch", "patch": patch}


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "drafts.db"
        self.repo = DraftRepository(self.path)
        self.draft = self.repo.create()
        self.id = self.draft["draftId"]

    def test_user_wins_over_agent_fields_and_preserves_unrelated_edits(self):
        self.repo.update(self.id, 1, definition(name="Agent 名称", description="Agent 说明"), actor="agent")
        self.repo.update(self.id, 2, {"type": "parameter.value", "id": "componentLength", "value": 3000}, actor="agent")
        saved = self.repo.update(self.id, 1, definition(name="用户名称"))
        self.assertTrue(saved["reconciled"])
        self.assertEqual(saved["document"]["revision"], 4)
        self.assertEqual(saved["document"]["definition"]["name"], "用户名称")
        self.assertEqual(saved["document"]["definition"]["description"], "Agent 说明")
        self.assertEqual(saved["document"]["parameters"][0]["value"], 3000)
        self.assertEqual([c["actor"] for c in saved["changes"]], ["agent", "agent", "user"])

    def test_stale_agent_and_other_user_cannot_overwrite_user(self):
        self.repo.update(self.id, 1, definition(name="用户名称"))
        for actor in ("agent", "user"):
            with self.assertRaises(ComponentError) as caught:
                self.repo.update(self.id, 1, definition(name="过期修改"), actor=actor)
            self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.repo.get(self.id)["document"]["definition"]["name"], "用户名称")

    def test_unknown_history_and_reset_are_never_replayed(self):
        self.repo.update(self.id, 1, definition(name="Agent"), actor="agent")
        with self.assertRaises(ComponentError):
            self.repo.update(self.id, 1, {"type": "reset"})
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM component_draft_changes")
        with self.assertRaises(ComponentError):
            self.repo.update(self.id, 1, definition(name="不应覆盖"))

    def test_deleted_target_is_reported_without_losing_existing_document(self):
        data = self.draft["document"]
        data["assembly"]["members"] = [member("a")]
        draft = self.repo.create(document=ComponentDocument.model_validate(data))
        self.repo.update(draft["draftId"], 1, {"type": "member.remove", "id": "a"}, actor="agent")
        with self.assertRaises(ComponentError) as caught:
            self.repo.update(draft["draftId"], 1, {"type": "member.patch", "id": "a", "patch": {"role": "用户角色"}})
        self.assertEqual(caught.exception.code, "EDIT_TARGET_REMOVED")
        self.assertEqual(caught.exception.current["document"]["revision"], 2)

    def test_changes_survive_restart_and_noop_has_no_false_diff(self):
        saved = self.repo.update(self.id, 1, definition(name="Agent 名称"), actor="agent")
        self.assertEqual(len(saved["changes"]), 1)
        event = saved["changes"][0]
        self.assertEqual((event["fromRevision"], event["revision"], event["actor"]), (1, 2, "agent"))
        item = event["items"][0]
        self.assertEqual(item["path"], ["definition", "name"])
        self.assertEqual(item["before"], "横梁组件")
        self.assertEqual(item["after"], "Agent 名称")
        self.assertEqual(item["label"], "组件定义 / 名称")
        self.assertTrue(event["createdAt"].endswith("Z"))
        restarted = DraftRepository(self.path)
        self.assertEqual(restarted.get(self.id, after_revision=1)["changes"], [event])
        self.assertEqual(restarted.get(self.id, after_revision=2)["changes"], [])
        noop = restarted.update(self.id, 2, definition(name="Agent 名称"), actor="agent")
        self.assertEqual(noop["document"]["revision"], 2)
        self.assertEqual(noop["changes"], [])

    def test_web_route_uses_user_priority_and_exposes_changes(self):
        self.repo.update(self.id, 1, definition(name="Agent 名称"), actor="agent")
        with TestClient(create_app(dist_dir=None, draft_db=self.path)) as client:
            response = client.post(f"/api/component-drafts/{self.id}/actions", json={
                "expectedRevision": 1, "action": definition(name="网页名称"),
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["reconciled"])
            changes = client.get(f"/api/component-drafts/{self.id}?afterRevision=1").json()["changes"]
            self.assertEqual([c["actor"] for c in changes], ["agent", "user"])
            self.assertEqual(client.get(f"/api/component-drafts/{self.id}?afterRevision=3").json()["changes"], [])

    def test_user_and_agent_race_always_retains_user_value(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        other = DraftRepository(self.path)
        barrier = Barrier(2)

        def edit(repository, actor, name):
            barrier.wait()
            try:
                return repository.update(self.id, 1, definition(name=name), actor=actor)
            except ComponentError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            user = pool.submit(edit, self.repo, "user", "用户最终名称")
            agent = pool.submit(edit, other, "agent", "Agent 名称")
            self.assertIsInstance(user.result(), dict)
            agent_result = agent.result()
        if isinstance(agent_result, ComponentError):
            self.assertEqual(agent_result.code, "REVISION_CONFLICT")
        self.assertEqual(self.repo.get(self.id)["document"]["definition"]["name"], "用户最终名称")

    def test_recent_history_is_bounded_and_structural_diff_is_readable(self):
        for revision in range(1, 53):
            self.repo.update(self.id, revision, definition(name=str(revision)), actor="agent")
        result = self.repo.get(self.id)
        self.assertTrue(result["changesTruncated"])
        self.assertEqual(len(result["changes"]), 50)
        self.assertEqual(result["changes"][0]["revision"], 4)
        # Priority detection uses all history, not the truncated display window.
        result = self.repo.update(self.id, 1, definition(name="用户优先"))
        self.assertTrue(result["reconciled"])
        data = self.draft["document"]
        data["assembly"]["members"] = [member("a")]
        draft = self.repo.create(document=ComponentDocument.model_validate(data))
        deleted = self.repo.update(draft["draftId"], 1, {"type": "member.remove", "id": "a"}, actor="agent")
        diff = deleted["changes"][0]["items"][0]
        self.assertEqual(diff["path"], ["assembly", "members", "a"])
        self.assertEqual(diff["kind"], "removed")
        self.assertIsNone(diff["after"])
        self.assertEqual(diff["before"]["id"], "a")


if __name__ == "__main__":
    unittest.main()
