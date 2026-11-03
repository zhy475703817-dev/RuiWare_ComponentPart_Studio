from dataclasses import replace
import json
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.components.repository import DraftRepository
from backend.app.components.models import ComponentAction
from backend.tests.test_library_cleanup import LibraryCleanupFixture


class ReferenceRegistryTests(LibraryCleanupFixture, unittest.TestCase):
    def registry_documents(self):
        return [json.loads(payload) for (_, key), payload in list(self.storage.objects.items())
                if '/reference-registry/' in key]

    def test_startup_backfills_preexisting_draft_and_uploads_only_reference_data(self):
        old = self.seed('old')
        repository = DraftRepository(self.settings.draft_db)
        draft = repository.create(name='已有草稿')
        repository.update(draft['draftId'], 1, ComponentAction.validate_python(
            {'type': 'member.add', 'member': self.member(old)}))
        with self.client() as client:
            for _ in range(300):
                if any(source['references'] for doc in self.registry_documents() for source in doc['sources']):
                    break
                time.sleep(.01)
            else:
                self.fail('startup did not backfill existing drafts')
            doc = self.registry_documents()[0]
            source = doc['sources'][0]
            self.assertEqual(source['references'][0]['publicationId'], old['publicationId'])
            self.assertEqual(set(source), {'kind', 'id', 'name', 'references'})
            self.assertNotIn('parameterValues', json.dumps(doc))
            self.assertNotIn('secret_key', json.dumps(doc))
            self.assertEqual(client.post('/api/template-library/references/sync').json()['status'], 'synchronized')
            written = len(self.storage.writes)
            client.app.state.reference_registry.sync()
            self.assertEqual(len(self.storage.writes), written, 'unchanged polling must not upload again')

    def test_mcp_member_edits_use_same_registration_and_release_removed_reference(self):
        old = self.seed('old')
        self.seed('new', version=2)
        app = create_app(self.settings, dist_dir=None)
        with TestClient(app, base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)) as client:
            draft = self.draft(client, [self.member(old)])
            self.sync(client)
            response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'component_remove_member', 'arguments': {'draftId': draft['draftId'],
                        'expectedRevision': draft['document']['revision'], 'memberId': 'old'}}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['result'].get('isError', False), response.text)
            self.sync(client)
            self.assertFalse(any(source['references'] for doc in self.registry_documents() for source in doc['sources']))

    def test_edits_during_upload_remain_pending_then_next_sync_catches_up(self):
        old = self.seed('old')
        with self.client() as client:
            draft = self.draft(client, [self.member(old)])
            original_put = self.storage.put_object
            def edit_during_put(bucket, key, *args, **kwargs):
                result = original_put(bucket, key, *args, **kwargs)
                client.app.state.drafts.update(draft['draftId'], draft['document']['revision'],
                    ComponentAction.validate_python({'type': 'member.remove', 'id': 'old'}))
                return result
            # Serialize with any startup sync before injecting the concurrent edit.
            self.sync(client)
            with patch.object(self.storage, 'put_object', side_effect=edit_during_put):
                status = client.app.state.reference_registry.sync(force=True)
            self.assertEqual(status['status'], 'pending')
            self.sync(client)
            self.assertFalse(any(source['references'] for doc in self.registry_documents() for source in doc['sources']))

    def test_no_storage_configuration_disables_sync_without_blocking_drafts(self):
        from backend.app.config import MinioSettings
        settings = replace(self.settings, minio=MinioSettings())
        with TestClient(create_app(settings, dist_dir=None)) as client:
            self.assertEqual(client.get('/api/template-library/references').json()['status'], 'disabled')
            self.assertEqual(client.post('/api/component-drafts', json={}).status_code, 200)
            self.assertEqual(client.post('/api/template-library/references/sync').json()['status'], 'disabled')
        self.assertEqual(self.storage.writes, [])

    def sync(self, client):
        response = client.post('/api/template-library/references/sync')
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(300):
            body = client.get('/api/template-library/references').json()
            if body.get('status') == 'synchronized':
                return body
            time.sleep(.01)
        self.fail(f'reference sync did not finish: {body}')

    def test_another_database_reports_offline_draft_references_without_blocking_cleanup(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as author:
            draft = self.draft(author, [self.member(old)])
            status = self.sync(author)
            author_id = status['clientId']
        reader_settings = replace(self.settings, draft_db=Path(self.tmp.name) / 'reader.sqlite3')
        with TestClient(create_app(reader_settings, dist_dir=None)) as reader:
            self.sync(reader)
            report = self.report(reader)
            row = next(item for item in report['items'] if item['publicationId'] == 'old')
            self.assertIn('shared-draft', row['reasons'])
            self.assertIn(draft['draftId'], [ref['id'] for ref in row['references']])
            self.assertGreaterEqual(report['sharedReferences']['clientCount'], 2)
            self.assertTrue(report['deletionEnabled'])
            self.assertEqual(row['status'], 'auto-clean')
        with self.client() as restarted:
            self.assertEqual(self.sync(restarted)['clientId'], author_id)

    def test_removing_last_reference_updates_registry_but_keeps_retry_snapshots(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            draft = self.draft(client, [self.member(old)])
            self.sync(client)
            client.app.state.drafts.prepare_publication(draft['draftId'], draft['document']['revision'])
            client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
                'expectedRevision': draft['document']['revision'], 'action': {'type': 'member.remove', 'id': 'old'}})
            self.sync(client)
        reader_settings = replace(self.settings, draft_db=Path(self.tmp.name) / 'reader.sqlite3')
        with TestClient(create_app(reader_settings, dist_dir=None)) as reader:
            report = self.report(reader)
            row = next(item for item in report['items'] if item['publicationId'] == 'old')
            self.assertIn('shared-snapshot', row['reasons'])
            self.assertNotIn('shared-draft', row['reasons'])

    def test_failed_upload_keeps_draft_and_previous_remote_references_then_retry_survives_restart(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            draft = self.draft(client, [self.member(old)])
            self.sync(client)
            original_put = self.storage.put_object
            def failing_put(bucket, key, *args, **kwargs):
                if '/reference-registry/' in key:
                    raise ConnectionError('private credentials')
                return original_put(bucket, key, *args, **kwargs)
            with patch.object(self.storage, 'put_object', side_effect=failing_put):
                response = client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
                    'expectedRevision': draft['document']['revision'], 'action': {'type': 'member.remove', 'id': 'old'}})
                self.assertEqual(response.status_code, 200)
                client.app.state.reference_registry.sync(force=True)
                status = client.get('/api/template-library/references').json()
                self.assertEqual(status['status'], 'error')
                self.assertNotIn('private', json.dumps(status))
                self.assertEqual(client.get(f"/api/component-drafts/{draft['draftId']}").json()['document']['assembly']['members'], [])
                rows = {item['publicationId']: item for item in self.report(client)['items']}
                self.assertIn('shared-draft', rows['old']['reasons'])
        with self.client() as restarted:
            self.sync(restarted)
            rows = {item['publicationId']: item for item in self.report(restarted)['items']}
            self.assertNotIn('shared-draft', rows['old']['reasons'])

    def test_background_retries_failed_registration_and_observes_changes_without_manual_sync(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with patch.object(self.storage, 'put_object', wraps=self.storage.put_object,
                          side_effect=ConnectionError('private connection details')) as upload:
            with self.client() as client:
                for _ in range(300):
                    if client.get('/api/template-library/references').json()['status'] == 'error':
                        break
                    time.sleep(.01)
                else:
                    self.fail('background registration failure was not reported')
                self.draft(client, [self.member(old)])
                upload.side_effect = None
                for _ in range(800):
                    docs = self.registry_documents()
                    if any(source['references'] for doc in docs for source in doc['sources']):
                        break
                    time.sleep(.01)
                else:
                    self.fail('draft references were not registered automatically after recovery')
                self.assertEqual(client.get('/api/template-library/references').json()['status'], 'synchronized')

    def test_cloned_database_uses_separate_identity(self):
        with self.client() as client:
            original = self.sync(client)['clientId']
        cloned_path = Path(self.tmp.name) / 'clone.sqlite3'
        shutil.copyfile(self.settings.draft_db, cloned_path)
        with TestClient(create_app(replace(self.settings, draft_db=cloned_path), dist_dir=None)) as clone:
            self.assertNotEqual(self.sync(clone)['clientId'], original)

    def test_corrupt_registry_blocks_candidates_and_stale_client_never_loses_protection(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            self.draft(client, [self.member(old)])
            self.sync(client)
        key = next(key for key in self.storage.objects if '/reference-registry/' in key[1])
        document = json.loads(self.storage.objects[key])
        document['updatedAt'] = '2020-01-01T00:00:00Z'
        self.storage.objects[key] = json.dumps(document).encode()
        reader_settings = replace(self.settings, draft_db=Path(self.tmp.name) / 'reader.sqlite3')
        with TestClient(create_app(reader_settings, dist_dir=None)) as reader:
            report = self.report(reader)
            row = next(item for item in report['items'] if item['publicationId'] == 'old')
            self.assertIn('shared-draft', row['reasons'])
            self.assertGreaterEqual(report['sharedReferences']['staleClientCount'], 1)
            self.storage.objects[key] = b'corrupt'
            report = self.report(reader)
            self.assertFalse(report['scanComplete'])
            self.assertEqual(report['summary']['reviewCount'], 0)

