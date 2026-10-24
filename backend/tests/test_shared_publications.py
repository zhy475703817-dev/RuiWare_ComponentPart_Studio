from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

from backend.app.config import load_settings
from backend.app.main import create_app
from backend.tests.storage_helpers import MemoryMinio, STORAGE_ENV


class SharedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.objects = MemoryMinio()
        self.env = patch.dict(os.environ, STORAGE_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.minio = patch('minio.Minio', return_value=self.objects)
        self.minio.start()
        self.addCleanup(self.minio.stop)

    def client(self, name):
        return TestClient(create_app(settings=replace(load_settings(), draft_db=Path(self.tmp.name) / f'{name}.sqlite3'), dist_dir=None))

    def publish(self, client, draft):
        return client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={'expectedRevision': draft['document']['revision']})

    def test_another_database_reads_downloads_and_nests_remote_component(self):
        with self.client('author') as author:
            draft = author.post('/api/component-drafts', json={'name': '共享底座'}).json()
            response = self.publish(author, draft)
            self.assertEqual(response.status_code, 200, response.text)
            original = response.json()
        with self.client('consumer') as consumer:
            listing = consumer.get('/api/published-components')
            self.assertEqual(listing.json()['count'], 1, listing.text)
            self.assertEqual(listing.json()['items'][0]['publicationId'], original['publicationId'])
            package = consumer.get(original['downloadUrl'])
            self.assertEqual(package.status_code, 200)
            self.assertIn('.rwpart', package.headers['content-disposition'])
            self.assertEqual(hashlib.sha256(package.content).hexdigest(), original['sha256'])
            with ZipFile(BytesIO(package.content)) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['templateKind'], 'component')
            detail = consumer.get(f"/api/published-components/{original['publicationId']}").json()
            self.assertEqual(detail['document'], original['document'])
            draft = consumer.post('/api/component-drafts', json={'name': '上层设备'}).json()
            added = consumer.post(f"/api/component-drafts/{draft['draftId']}/actions", json={'expectedRevision': 1, 'action': {
                'type': 'member.add', 'member': {
                    'id': 'base', 'name': '共享底座', 'code': 'BASE', 'role': 'base',
                    'template': {'templateKind': 'component', **{k: original[k] for k in ('publicationId','templateId','version','sha256')}},
                    'position': [0,0,0], 'rotation': [0,0,0], 'interfaces': [], 'parameterValues': {},
                }}})
            self.assertEqual(added.status_code, 200, added.text)
            nested = self.publish(consumer, added.json())
            self.assertEqual(nested.status_code, 200, nested.text)
            self.assertEqual(nested.json()['memberCount'], 1)
        with self.client('third') as third:
            self.assertEqual(third.get('/api/published-components').json()['count'], 2)
            self.assertEqual(third.get(f"/api/published-components/{nested.json()['publicationId']}").json()['document']['assembly']['members'][0]['template']['sha256'], original['sha256'])
        self.assertTrue(all(key.startswith('publishers/test-user/') for key in self.objects.writes))
        self.assertTrue(all(key.endswith(('template.rwpart','metadata.json')) for key in self.objects.writes))

    def test_failed_upload_is_not_published_and_retry_reuses_snapshot(self):
        with self.client('author') as client:
            draft = client.post('/api/component-drafts', json={'name': '保留草稿'}).json()
            self.objects.fail_metadata = True
            failure = self.publish(client, draft)
            self.assertEqual(failure.status_code, 503, failure.text)
            self.assertEqual(failure.json()['error']['code'], 'REMOTE_STORAGE_UNAVAILABLE')
            self.assertNotIn('private', failure.text)
            self.assertEqual(client.get('/api/published-components').json()['count'], 0)
            self.assertEqual(client.get(f"/api/component-drafts/{draft['draftId']}").status_code, 200)
            orphan_key = next(key for bucket,key in self.objects.objects if key.endswith('template.rwpart'))
            original_bytes = self.objects.objects['shared-templates', orphan_key]
            self.objects.fail_metadata = False
            success = self.publish(client, draft)
            self.assertEqual(success.status_code, 200, success.text)
            self.assertEqual(success.json()['sha256'], hashlib.sha256(original_bytes).hexdigest())
            writes = len(self.objects.writes)
            self.assertEqual(self.publish(client, draft).json(), success.json())
            self.assertEqual(len(self.objects.writes), writes)
            self.assertEqual(client.get('/api/published-components').json()['count'], 1)

    def test_corrupt_remote_package_is_rejected_instead_of_local_fallback(self):
        with self.client('author') as client:
            draft = client.post('/api/component-drafts', json={}).json()
            original = self.publish(client, draft).json()
            key = ('shared-templates', f"publishers/test-user/{original['publicationId']}/template.rwpart")
            self.objects.objects[key] = b'corrupt'
            response = client.get(original['downloadUrl'])
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(response.json()['error']['code'], 'REMOTE_STORAGE_INTEGRITY_ERROR')
            self.assertEqual(self.publish(client, draft).status_code, 502)

    def test_unconfigured_storage_cannot_claim_publication_success(self):
        with patch.dict(os.environ, {'RUIWARE_MINIO_ACCESS_KEY': ''}):
            with self.client('unconfigured') as client:
                draft = client.post('/api/component-drafts', json={}).json()
                response = self.publish(client, draft)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()['error']['code'], 'REMOTE_STORAGE_NOT_CONFIGURED')

    def test_explicit_migration_preserves_historical_bytes_and_nested_hashes(self):
        from backend.app.components.repository import DraftRepository

        settings = replace(load_settings(), draft_db=Path(self.tmp.name) / 'legacy.sqlite3')
        drafts = DraftRepository(settings.draft_db)
        first = drafts.create(name='Legacy base')
        snapshot = drafts.prepare_publication(first['draftId'], 1)
        payload = drafts.publication_package(snapshot['publicationId'])
        from backend.app.components.models import ComponentDocument
        parent = drafts.create(name='Legacy parent')
        document = parent['document']
        document['assembly']['members'] = [{
            'id': 'child', 'name': snapshot['name'], 'code': snapshot['code'], 'role': 'base',
            'template': {'templateKind': 'component', **{k: snapshot[k] for k in ('publicationId', 'templateId', 'version', 'sha256')}},
            'position': [0, 0, 0], 'rotation': [0, 0, 0], 'parameterValues': {}, 'interfaces': [],
        }]
        parent = drafts.create(document=ComponentDocument.model_validate(document))
        parent_snapshot = drafts.prepare_publication(parent['draftId'], 1)
        parent_payload = drafts.publication_package(parent_snapshot['publicationId'])
        with TestClient(create_app(settings=settings, dist_dir=None)) as client:
            # Startup must not silently upload prepared or historical snapshots.
            self.assertEqual(client.get('/api/published-components').json()['count'], 0)
            self.assertEqual(client.app.state.publications.migrate(), {'count': 2})
            self.assertEqual(client.get(snapshot['downloadUrl']).content, payload)
            self.assertEqual(client.get(parent_snapshot['downloadUrl']).content, parent_payload)
            detail = client.get(f"/api/published-components/{parent_snapshot['publicationId']}").json()
            self.assertEqual(detail['document']['assembly']['members'][0]['template']['sha256'], snapshot['sha256'])
            before = len(self.objects.writes)
            self.assertEqual(client.app.state.publications.migrate(), {'count': 2})
            self.assertEqual(len(self.objects.writes), before)

    def test_invalid_storage_endpoint_does_not_disable_draft_editing(self):
        with patch('minio.Minio', side_effect=ValueError('invalid endpoint')):
            with self.client('invalid-endpoint') as client:
                self.assertEqual(client.get('/api/health').status_code, 200)
                draft = client.post('/api/component-drafts', json={})
                self.assertEqual(draft.status_code, 200)
                response = self.publish(client, draft.json())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['code'], 'REMOTE_STORAGE_NOT_CONFIGURED')

    def test_oversized_package_cannot_commit_a_visible_unreadable_release(self):
        with self.client('oversized') as client:
            draft = client.post('/api/component-drafts', json={}).json()
            document = draft['document']
            document['rules'] = [{'id': str(index), 'expression': 'x' * 65536} for index in range(130)]
            response = client.post('/api/component-drafts', json={'document': document})
            self.assertEqual(response.status_code, 200, response.text[:200])
            response = self.publish(client, response.json())
            self.assertEqual(response.status_code, 422)
            self.assertEqual(client.get('/api/published-components').json()['count'], 0)
            self.assertEqual(self.objects.writes, [])

    def test_storage_object_keys_cannot_redirect_component_downloads(self):
        with self.client('author') as client:
            draft = client.post('/api/component-drafts', json={}).json()
            published = self.publish(client, draft).json()
            key = ('shared-templates', f"publishers/test-user/{published['publicationId']}/metadata.json")
            metadata = json.loads(self.objects.objects[key])
            metadata['objectKey'] = 'private/secrets.json'
            self.objects.objects[key] = json.dumps(metadata).encode()
            response = client.get(published['downloadUrl'])
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()['error']['code'], 'REMOTE_STORAGE_INTEGRITY_ERROR')


if __name__ == '__main__':
    unittest.main()
