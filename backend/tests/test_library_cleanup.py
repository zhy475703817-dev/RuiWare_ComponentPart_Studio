import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config import MinioSettings, Settings
from backend.app.main import create_app
from backend.tests.storage_helpers import MemoryMinio
from backend.tests.test_package_similarity import package as part_package


class LibraryCleanupFixture:
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = MemoryMinio()
        self.settings = Settings(draft_db=Path(self.tmp.name) / 'drafts.sqlite3', cleanup_dir=Path(self.tmp.name) / 'cleanup', minio=MinioSettings(
            endpoint='minio.test', access_key='test', secret_key='test', bucket='templates', prefix='publishers/test'))
        mock = patch('minio.Minio', return_value=self.storage)
        mock.start()
        self.addCleanup(mock.stop)

    def client(self):
        return TestClient(create_app(self.settings, dist_dir=None))

    def seed(self, identifier, *, template='beam', version=1, created='2020-01-01T00:00:00Z', kind='part'):
        docs = json.loads(Path(__file__).with_name('fixtures').joinpath('package-similarity-v1.json').read_text(encoding='utf-8'))
        docs['manifest.json'].update(id=template, code='BEAM')
        payload = identifier.encode() if kind == 'component' else part_package(docs, output_hash=hashlib.sha256(identifier.encode()).hexdigest())
        directory = f'publishers/test/{identifier}'
        item = dict(publicationId=identifier, templateId=template, templateKind=kind, version=version,
                    name='横梁', code='BEAM', createdAt=created, size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(), objectKey=directory + '/template.rwpart')
        self.storage.objects['templates', item['objectKey']] = payload
        self.storage.objects['templates', directory + '/metadata.json'] = json.dumps(item).encode()
        return item

    def member(self, item):
        return dict(id=item['publicationId'], name='成员', code='BEAM', role='成员', position=[0, 0, 0], rotation=[0, 0, 0],
                    interfaces=[], parameterValues={}, template={key: item[key] for key in ('publicationId', 'templateId', 'templateKind', 'version', 'sha256')})

    def draft(self, client, members):
        draft = client.post('/api/component-drafts', json={'name': '测试组件'}).json()
        for member in members:
            response = client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
                'expectedRevision': draft['document']['revision'], 'action': {'type': 'member.add', 'member': member}})
            self.assertEqual(response.status_code, 200, response.text)
            draft = response.json()
        return draft

    def publish(self, client, draft):
        response = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={'expectedRevision': draft['document']['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def report(self, client):
        response = client.post('/api/template-library/cleanup-preview')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return response.json()


class LibraryCleanupTests(LibraryCleanupFixture, unittest.TestCase):
    def test_old_release_is_strict_candidate_and_preview_never_changes_objects(self):
        self.seed('old')
        self.seed('new', version=2)
        self.seed('recent-old', template='recent', created='2999-01-01T00:00:00Z')
        self.seed('recent-new', template='recent', version=2)
        before = dict(self.storage.objects)
        with self.client() as client:
            report = self.report(client)
            rows = {item['publicationId']: item for item in report['items']}
            self.assertEqual(rows['old']['status'], 'auto-clean')
            self.assertEqual(rows['old']['candidateClass'], 'old-release')
            self.assertEqual(rows['new']['status'], 'awaiting-confirmation')
            self.assertEqual(rows['new']['candidateClass'], 'high-similarity')
            self.assertEqual(rows['recent-old']['status'], 'auto-clean')
            self.assertEqual(report['summary']['strictCandidateBytes'], rows['old']['size'] + rows['recent-old']['size'])
            self.assertEqual(report['summary']['totalBytes'], sum(item['size'] for item in report['items']))
            self.assertEqual(report['summary']['totalCount'], 4)
            self.assertTrue(report['scanComplete'])
            self.assertTrue(report['deletionEnabled'])
            self.assertEqual(report['referenceScope'], 'published-local-and-registered')
            self.assertEqual(client.delete('/api/template-library/cleanup-preview').status_code, 405)
        self.assertEqual({key: value for key, value in self.storage.objects.items()
                          if '/reference-registry/' not in key[1]}, before)

    def test_local_drafts_and_unpublished_snapshots_protect_old_releases(self):
        old = self.seed('old')
        self.seed('new', version=2)
        retry = self.seed('retry-old', template='retry')
        self.seed('retry-new', template='retry', version=2)
        with self.client() as client:
            self.draft(client, [self.member(old)])
            draft = self.draft(client, [self.member(retry)])
            client.app.state.drafts.prepare_publication(draft['draftId'], draft['document']['revision'])
            client.post(f"/api/component-drafts/{draft['draftId']}/actions", json={
                'expectedRevision': draft['document']['revision'], 'action': {'type': 'member.remove', 'id': 'retry-old'}})
            rows = {item['publicationId']: item for item in self.report(client)['items']}
            self.assertIn('local-draft', rows['old']['reasons'])
            self.assertIn('local-snapshot', rows['retry-old']['reasons'])
            self.assertTrue(rows['old']['references'])

    def test_nested_published_components_protect_parts_and_component_history(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            base_draft = self.draft(client, [self.member(old)])
            base = self.publish(client, base_draft)
            parent = self.publish(client, self.draft(client, [self.member(base)]))
            client.post(f"/api/component-drafts/{base_draft['draftId']}/actions", json={
                'expectedRevision': base_draft['document']['revision'], 'action': {'type': 'definition.patch', 'patch': {'name': '底座新版'}}})
            changed = client.get(f"/api/component-drafts/{base_draft['draftId']}").json()
            self.publish(client, changed)
            rows = {item['publicationId']: item for item in self.report(client)['items']}
            self.assertIn('published-component', rows['old']['reasons'])
            self.assertIn('published-component', rows[base['publicationId']]['reasons'])
            self.assertIn(parent['publicationId'], [ref['id'] for ref in rows[base['publicationId']]['references']])

    def test_unreadable_component_blocks_review_candidates_and_sanitizes_error(self):
        self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            publication = self.publish(client, self.draft(client, []))
            self.storage.fail_get.add(f"publishers/test/{publication['publicationId']}/template.rwpart")
            report = self.report(client)
            self.assertFalse(report['scanComplete'])
            self.assertGreater(report['summary']['blockedCount'], 0)
            self.assertEqual(next(item for item in report['items'] if item['publicationId'] == 'old')['status'], 'blocked')
            self.assertNotIn('private', json.dumps(report))
            self.assertTrue(report['issues'])

    def test_mismatched_dependency_makes_scan_incomplete(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            self.draft(client, [{**self.member(old), 'template': {**self.member(old)['template'], 'sha256': '0' * 64}}])
            report = self.report(client)
            self.assertFalse(report['scanComplete'])
            self.assertGreater(report['summary']['blockedCount'], 0)
            self.assertIn('REFERENCE_MISMATCH', [issue['code'] for issue in report['issues']])

    def test_missing_nested_component_blocks_candidates(self):
        self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            missing = {'publicationId': 'missing-component', 'templateKind': 'component', 'templateId': 'absent', 'version': 1, 'sha256': 'a' * 64}
            self.draft(client, [self.member(missing)])
            report = self.report(client)
            self.assertFalse(report['scanComplete'])
            self.assertGreater(report['summary']['blockedCount'], 0)
            self.assertIn('REFERENCE_MISSING', [issue['code'] for issue in report['issues']])

    def test_local_scan_failure_blocks_candidates_without_exposing_exception(self):
        self.seed('old')
        self.seed('new', version=2)
        with self.client() as client, patch.object(client.app.state.drafts, 'cleanup_sources', side_effect=ValueError('private database details')):
            report = self.report(client)
            self.assertFalse(report['scanComplete'])
            self.assertGreater(report['summary']['blockedCount'], 0)
            self.assertNotIn('private', json.dumps(report))

    def test_storage_failure_has_no_misleading_report(self):
        self.storage.fail_list = True
        with self.client() as client:
            response = client.post('/api/template-library/cleanup-preview')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'error': {'code': 'REMOTE_STORAGE_UNAVAILABLE'}})

    def test_old_release_candidate_is_not_hidden_by_a_recent_date(self):
        self.seed('old', created='2026-10-02T00:00:00Z')
        self.seed('new', version=2)
        with self.client() as client:
            rows = {item['publicationId']: item for item in self.report(client)['items']}
            self.assertEqual(rows['old']['status'], 'auto-clean')


if __name__ == '__main__':
    unittest.main()
