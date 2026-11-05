import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from backend.app.library_cleanup import cleanup_preview
from backend.app.config import MinioSettings
from backend.app.published_storage import PublishedStorage
from backend.tests.storage_helpers import MemoryMinio
from backend.tests.test_package_similarity import package


class LibraryCleanupCandidateTests(unittest.TestCase):
    def setUp(self):
        self.minio = MemoryMinio()
        self.storage = PublishedStorage(MinioSettings(bucket="templates", prefix="publishers/test"), self.minio)

    def seed(self, identifier, *, template_id=None, version=1, payload=None, kind="part"):
        payload = package() if payload is None else payload
        directory = f"publishers/test/{identifier}"
        metadata = {
            "publicationId": identifier,
            "templateId": template_id or identifier,
            "templateKind": kind,
            "version": version,
            "code": "BEAM",
            "name": "横梁",
            "createdAt": f"2026-01-0{version}T00:00:00Z",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "objectKey": directory + "/template.rwpart",
        }
        self.minio.objects["templates", metadata["objectKey"]] = payload
        self.minio.objects["templates", directory + "/metadata.json"] = json.dumps(metadata).encode()
        return metadata

    def scan(self):
        drafts = SimpleNamespace(cleanup_sources=lambda: [])
        publications = SimpleNamespace()
        return cleanup_preview(self.storage, drafts, publications)

    def test_latest_version_is_kept_and_old_version_is_strict_candidate(self):
        old = self.seed("old", template_id="beam", version=1)
        latest = self.seed("latest", template_id="beam", version=2)

        report = self.scan()

        rows = {item["publicationId"]: item for item in report["items"]}
        self.assertEqual(rows["old"]["candidateClass"], "old-release")
        self.assertEqual(rows["latest"]["candidateClass"], "none")
        self.assertEqual(rows["latest"]["status"], "retained")
        self.assertTrue(report["scanComplete"])

    def test_same_package_hash_is_exact_duplicate_even_with_different_template_ids(self):
        payload = package()
        self.seed("copy-a", template_id="beam-a", payload=payload)
        self.seed("copy-b", template_id="beam-b", payload=payload)

        rows = {item["publicationId"]: item for item in self.scan()["items"]}

        self.assertEqual(rows["copy-a"]["candidateClass"], "exact-duplicate")
        self.assertEqual(rows["copy-b"]["candidateClass"], "none")

    def test_semantically_identical_different_package_bytes_are_high_similarity(self):
        docs = json.loads(Path(__file__).with_name("fixtures").joinpath("package-similarity-v1.json").read_text(encoding="utf-8"))
        docs["manifest.json"]["id"] = "different-package-id"
        docs["manifest.json"]["author"] = "another-author"
        self.seed("semantic-a", template_id="beam-a", payload=package())
        self.seed("semantic-b", template_id="beam-b", payload=package(docs))

        rows = {item["publicationId"]: item for item in self.scan()["items"]}

        self.assertEqual(rows["semantic-a"]["candidateClass"], "high-similarity")
        self.assertEqual(rows["semantic-b"]["candidateClass"], "high-similarity")
        self.assertEqual(rows["semantic-a"]["scores"]["S"], 100.0)

    def test_unclassified_package_is_reported_without_similarity_score(self):
        self.seed("bad", payload=b"not a zip")

        item = self.scan()["items"][0]

        self.assertEqual(item["candidateClass"], "unclassified")
        self.assertEqual(item["similarityReason"], "PACKAGE_INVALID")


if __name__ == "__main__":
    unittest.main()
