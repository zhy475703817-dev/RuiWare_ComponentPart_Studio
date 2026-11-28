from base64 import b64encode
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

from backend.app.components.models import ComponentDocument
from backend.app.components.qualification_policy import QUALIFICATION_VERSION, digest
from backend.app.components.repository import DraftRepository
from backend.app.components.service import create_component_document


class ComponentPublicationPackageTests(unittest.TestCase):
    def test_package_matches_legacy_bytes_with_and_without_evidence(self):
        for with_evidence, expected_hash in (
            (False, "0b036633c668a3568e197e9e8c801aa70bc9964ae84420d986b72ac434500847"),
            (True, "13d20e185fcef69a882c91873754b769d67b98eb99566466ce7d1a303e06347c"),
        ):
            with self.subTest(with_evidence=with_evidence), TemporaryDirectory() as directory:
                data = create_component_document().model_dump(by_alias=True)
                if with_evidence:
                    content = b"component evidence\n"
                    data["definition"]["evidenceFiles"] = [{
                        "id": "drawing", "fileName": "安装说明.txt", "mediaType": "text/plain",
                        "size": len(content), "sha256": sha256(content).hexdigest(),
                        "dataUrl": f"data:text/plain;base64,{b64encode(content).decode('ascii')}",
                    }]
                repository = DraftRepository(Path(directory) / "drafts.sqlite3")
                with patch("backend.app.components.repository.uuid4", side_effect=[UUID(int=1), UUID(int=2)]), \
                        patch.object(repository, "_now", return_value="2026-11-28T00:00:00+00:00"):
                    draft = repository.create(document=ComponentDocument.model_validate(data))
                    draft_id = draft["draftId"]
                    config = repository.qualification(draft_id)["config"]
                    repository.save_qualification(draft_id, {
                        "status": "passed", "revision": 1, "policyVersion": QUALIFICATION_VERSION,
                        "inputHash": digest(draft["document"]), "configHash": digest(config), "cases": [],
                    })
                    snapshot = repository.prepare_publication(draft_id, 1)
                    payload = repository.publication_package(snapshot["publicationId"])
                    self.assertEqual(sha256(payload).hexdigest(), expected_hash)
                    self.assertEqual(snapshot["sha256"], expected_hash)
                    self.assertEqual(repository.prepare_publication(draft_id, 1), snapshot)
