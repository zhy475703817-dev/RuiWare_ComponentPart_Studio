from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.app.components.repository import DraftRepository
from backend.app.components.service import ComponentError


class DraftEvidenceExportTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        path = Path(directory) / "drafts.sqlite3"
        self.drafts = DraftRepository(path)
        with closing(self.drafts._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        self.writer = DraftRepository(path)
        draft = self.drafts.create()
        self.draft_id = draft["draftId"]
        self.evidence_id = str(uuid4())
        reference = self.drafts.save_evidence(self.draft_id, self.evidence_id, "note.txt", "text/plain", b"abc")
        draft = self.drafts.update(self.draft_id, draft["document"]["revision"], {
            "type": "definition.patch", "patch": {"name": "original", "evidenceFiles": [reference]},
        })
        self.revision = draft["document"]["revision"]

    def test_export_retains_original_bytes_when_an_edit_removes_attachment(self):
        read = self.drafts._read

        def remove_evidence_after_read(connection, draft_id):
            document = read(connection, draft_id)
            self.writer.update(draft_id, self.revision, {
                "type": "definition.patch", "patch": {"name": "changed", "evidenceFiles": []},
            })
            return document

        with patch.object(self.drafts, "_read", side_effect=remove_evidence_after_read):
            exported = self.drafts.export(self.draft_id)

        self.assertEqual(exported["name"], "original")
        self.assertEqual(exported["stages"]["definition"]["evidenceFiles"][0]["dataUrl"], "data:text/plain;base64,YWJj")
        self.assertEqual(self.drafts.get(self.draft_id)["document"]["definition"]["evidenceFiles"], [])

    def test_export_rejects_replaced_content_with_same_size(self):
        self.writer.save_evidence(self.draft_id, self.evidence_id, "note.txt", "text/plain", b"xyz")

        with self.assertRaises(ComponentError) as caught:
            self.drafts.export(self.draft_id)

        self.assertEqual(caught.exception.code, "INVALID_EVIDENCE")

    def test_staggered_uploads_survive_edits_until_referenced(self):
        first = self.drafts.save_evidence(self.draft_id, str(uuid4()), "first.txt", "text/plain", b"first")
        renamed = self.writer.update(self.draft_id, self.revision, {
            "type": "definition.patch", "patch": {"name": "renamed before saving files"},
        })
        second = self.drafts.save_evidence(self.draft_id, str(uuid4()), "second.txt", "text/plain", b"second")

        saved_first = self.writer.update(self.draft_id, renamed["document"]["revision"], {
            "type": "definition.patch", "patch": {"evidenceFiles": [first]},
        })
        saved_both = self.writer.update(self.draft_id, saved_first["document"]["revision"], {
            "type": "definition.patch", "patch": {"evidenceFiles": [first, second]},
        })

        definition = saved_both["document"]["definition"]
        self.assertEqual(definition["name"], "renamed before saving files")
        self.assertEqual(definition["evidenceFiles"], [first, second])
        self.assertEqual(self.drafts.get_evidence(self.draft_id, first["id"])["content"], b"first")
        self.assertEqual(self.drafts.get_evidence(self.draft_id, second["id"])["content"], b"second")
        with self.assertRaises(ComponentError) as caught:
            self.drafts.get_evidence(self.draft_id, self.evidence_id)
        self.assertEqual(caught.exception.code, "EVIDENCE_NOT_FOUND")

    def test_discarding_unreferenced_upload_preserves_committed_file_and_releases_quota(self):
        file = b"x" * (5 * 1024 * 1024)
        discarded = str(uuid4())
        self.drafts.save_evidence(self.draft_id, discarded, "discarded.bin", "application/octet-stream", file)
        self.drafts.save_evidence(self.draft_id, str(uuid4()), "pending.bin", "application/octet-stream", file)
        with self.assertRaises(ComponentError) as caught:
            self.drafts.save_evidence(self.draft_id, str(uuid4()), "blocked.bin", "application/octet-stream", file)
        self.assertEqual(caught.exception.code, "EVIDENCE_TOTAL_TOO_LARGE")

        self.writer.delete_evidence(self.draft_id, self.evidence_id, unreferenced_only=True)
        self.writer.delete_evidence(self.draft_id, discarded, unreferenced_only=True)

        self.assertEqual(self.drafts.get_evidence(self.draft_id, self.evidence_id)["content"], b"abc")
        with self.assertRaises(ComponentError) as caught:
            self.drafts.get_evidence(self.draft_id, discarded)
        self.assertEqual(caught.exception.code, "EVIDENCE_NOT_FOUND")
        replacement = self.drafts.save_evidence(self.draft_id, str(uuid4()), "replacement.bin", "application/octet-stream", file)
        self.assertEqual(replacement["size"], len(file))
