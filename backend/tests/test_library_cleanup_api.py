import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config import MinioSettings, Settings
from backend.app.main import create_app
from backend.tests.storage_helpers import MemoryMinio
from backend.tests.test_package_similarity import package
from backend.tests.test_library_cleanup import LibraryCleanupFixture
import hashlib


class CleanupApiTests(unittest.TestCase):
    def test_cleanup_backs_up_then_deletes_and_can_restore(self):
        with TemporaryDirectory() as directory:
            storage = MemoryMinio()
            settings = Settings(draft_db=Path(directory) / "drafts.sqlite3", cleanup_dir=Path(directory) / "cleanup",
                                minio=MinioSettings(endpoint="minio.test", access_key="access", secret_key="secret",
                                                     bucket="templates", prefix="publishers/test"))
            docs = json.loads(Path(__file__).with_name('fixtures').joinpath('package-similarity-v1.json').read_text(encoding='utf-8'))
            docs['manifest.json'].update(id='beam', code='BEAM')
            payload = package(docs)
            object_key = "publishers/test/old/template.rwpart"
            metadata = {"publicationId": "old", "templateId": "beam", "templateKind": "part", "version": 1,
                        "code": "BEAM", "name": "横梁", "createdAt": "2020-01-01T00:00:00Z",
                        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "objectKey": object_key}
            storage.objects["templates", object_key] = payload
            storage.objects["templates", "publishers/test/old/metadata.json"] = json.dumps(metadata).encode()
            storage.objects["templates", "publishers/test/new/template.rwpart"] = payload
            storage.objects["templates", "publishers/test/new/metadata.json"] = json.dumps({**metadata, "publicationId": "new", "version": 2, "objectKey": "publishers/test/new/template.rwpart", "createdAt": "2021-01-01T00:00:00Z"}).encode()
            with patch("minio.Minio", return_value=storage), TestClient(create_app(settings, dist_dir=None)) as client:
                preview = client.post("/api/template-library/cleanup-preview").json()
                old = next(item for item in preview["items"] if item["publicationId"] == "old")
                result = client.post("/api/template-library/cleanup", json={"scanId": preview["scanId"], "publicationIds": ["old"]})
                self.assertEqual(result.status_code, 200, result.text)
                backup_id = result.json()["items"][0]["backupId"]
                self.assertNotIn(("templates", object_key), storage.objects)
                self.assertEqual(client.get(f"/api/template-library/backups/{backup_id}/download").status_code, 200)
                storage.objects["templates", object_key] = b"unrelated object"
                conflict = client.post(f"/api/template-library/backups/{backup_id}/restore")
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(storage.objects["templates", object_key], b"unrelated object")
                storage.objects.pop(("templates", object_key))
                restored = client.post(f"/api/template-library/backups/{backup_id}/restore")
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertEqual(storage.objects["templates", object_key], payload)
                self.assertEqual(old["candidateClass"], "old-release")


class CleanupSafetyTests(LibraryCleanupFixture, unittest.TestCase):
    def test_confirmation_rejects_publication_moved_to_another_account(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            scan = self.report(client)
            package = self.storage.objects.pop(('templates', old['objectKey']))
            self.storage.objects.pop(('templates', 'publishers/test/old/metadata.json'))
            moved = 'publishers/another/old/template.rwpart'
            self.storage.objects['templates', moved] = package
            self.storage.objects['templates', 'publishers/another/old/metadata.json'] = json.dumps({**old, 'objectKey': moved}).encode()
            result = client.post('/api/template-library/cleanup', json={'scanId': scan['scanId'], 'publicationIds': ['old']})
            self.assertEqual(result.status_code, 409, result.text)
            self.assertEqual(result.json()['items'][0]['errorCode'], 'PUBLICATION_CHANGED')
            self.assertIn(('templates', moved), self.storage.objects)

    def test_restore_conditional_write_never_overwrites_concurrent_writer(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            scan = self.report(client)
            result = client.post('/api/template-library/cleanup', json={'scanId': scan['scanId'], 'publicationIds': ['old']})
            backup = result.json()['items'][0]['backupId']
            put = self.storage._put_object

            def occupy_then_put(bucket, key, data, headers=None):
                self.storage.objects[bucket, key] = b'concurrent publication'
                return put(bucket, key, data, headers=headers)

            with patch.object(self.storage, '_put_object', side_effect=occupy_then_put):
                restored = client.post(f'/api/template-library/backups/{backup}/restore')
            self.assertEqual(restored.status_code, 409, restored.text)
            self.assertEqual(self.storage.objects['templates', old['objectKey']], b'concurrent publication')

    def test_restore_can_retry_after_package_write_succeeds_but_marker_fails(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            scan = self.report(client)
            result = client.post('/api/template-library/cleanup', json={'scanId': scan['scanId'], 'publicationIds': ['old']})
            backup = result.json()['items'][0]['backupId']
            self.storage.fail_metadata = True
            failed = client.post(f'/api/template-library/backups/{backup}/restore')
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(hashlib.sha256(self.storage.objects['templates', old['objectKey']]).hexdigest(), old['sha256'])
            self.storage.fail_metadata = False
            retried = client.post(f'/api/template-library/backups/{backup}/restore')
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertIn(('templates', 'publishers/test/old/metadata.json'), self.storage.objects)

    def test_all_high_similarity_candidates_cannot_be_deleted_together(self):
        left = self.seed('left', template='a')
        right = self.seed('right', template='b')
        with self.client() as client:
            scan = self.report(client)
            result = client.post('/api/template-library/cleanup', json={'scanId': scan['scanId'], 'publicationIds': ['left', 'right']})
            self.assertEqual(result.status_code, 409, result.text)
            self.assertTrue(all(item['errorCode'] == 'CLEANUP_KEEP_ONE_REQUIRED' for item in result.json()['items']))
            for item in (left, right):
                self.assertIn(('templates', item['objectKey']), self.storage.objects)

    def test_final_audit_failure_still_prunes_active_catalog(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        with self.client() as client:
            cache = client.app.state.library_cache
            cache.stage('part', [{**item, 'downloadUrl': '/download'} for item in (old, latest)])
            cache.apply()
            scan = self.report(client)
            append = client.app.state.cleanup_store.append_audit

            def fail_final(entry):
                if entry['status'] == 'deleted':
                    raise OSError('disk full')
                append(entry)

            with patch.object(client.app.state.cleanup_store, 'append_audit', side_effect=fail_final):
                result = client.post('/api/template-library/cleanup', json={'scanId': scan['scanId'], 'publicationIds': ['old']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['items'][0]['warningCode'], 'CLEANUP_LOCAL_UPDATE_FAILED')
            self.assertNotIn('old', [item['publicationId'] for item in cache.snapshot()['items']])


if __name__ == "__main__":
    unittest.main()
