import json
import unittest
from unittest.mock import patch

from backend.app.library import PublishedTemplate
from backend.tests.test_library_cleanup import LibraryCleanupFixture
from backend.tests.test_preview import STL


class SyncCleanupTests(LibraryCleanupFixture, unittest.TestCase):
    def sync(self, client):
        with patch('backend.app.library_sync.read_package', return_value=([], [], None, None)):
            client.portal.call(client.app.state.library_sync.sync_parts)
        return client.app.state.library_sync.cleanup

    def prepare_latest(self, client, item):
        publication = PublishedTemplate.model_validate({**item, 'downloadUrl': '/download'})
        client.app.state.library_cache.save_model(publication, STL)

    def copy_package(self, source, target):
        self.storage.objects['templates', target['objectKey']] = self.storage.objects['templates', source['objectKey']]
        target.update(sha256=source['sha256'], size=source['size'])
        self.storage.objects['templates', target['objectKey'].rsplit('/', 1)[0] + '/metadata.json'] = json.dumps(target).encode()

    def test_sync_deletes_old_release_even_when_referenced_and_keeps_latest(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        with self.client() as client:
            self.draft(client, [self.member(old)])
            self.prepare_latest(client, latest)
            result = self.sync(client)
            self.assertEqual([item['publicationId'] for item in result['deleted']], ['old'])
            self.assertNotIn(('templates', old['objectKey']), self.storage.objects)
            self.assertIn(('templates', latest['objectKey']), self.storage.objects)
            self.assertEqual(len(client.app.state.cleanup_store.list_backups()), 1)
            self.assertEqual(client.get('/api/template-library/cleanup-audit').status_code, 200)
            report = client.post('/api/template-library/cleanup-preview').json()
            self.assertTrue(report['scanComplete'], report['issues'])
            self.assertIn('REFERENCE_MISSING', [issue['code'] for issue in report['issues']])

    def test_incomplete_scan_blocks_cleanup_but_keeps_reading_catalog(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        with self.client() as client:
            self.prepare_latest(client, latest)
            with patch.object(client.app.state.drafts, 'cleanup_sources', side_effect=OSError('private')):
                result = self.sync(client)
            self.assertEqual(result['status'], 'blocked')
            self.assertIn(('templates', old['objectKey']), self.storage.objects)

    def test_backup_failure_never_deletes_old_release(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        with self.client() as client:
            self.prepare_latest(client, latest)
            with patch('backend.app.library_cleanup.write_backup', side_effect=OSError('disk full')):
                result = self.sync(client)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['failed'][0]['errorCode'], 'BACKUP_FAILED')
            self.assertIn(('templates', old['objectKey']), self.storage.objects)
            snapshot = client.app.state.library_sync.snapshot()
            self.assertEqual(snapshot['errors'], [])
            self.assertEqual([item['publicationId'] for item in snapshot['items']], ['new'])

    def test_cleanup_scan_save_failure_does_not_block_catalog_updates(self):
        self.seed('old')
        latest = self.seed('new', version=2)
        with self.client() as client:
            self.prepare_latest(client, latest)
            with patch.object(client.app.state.cleanup_store, 'save_scan', side_effect=OSError('private path')):
                result = self.sync(client)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['errorCode'], 'CLEANUP_FAILED')
            snapshot = client.app.state.library_sync.snapshot()
            self.assertEqual(snapshot['errors'], [])
            self.assertEqual([item['publicationId'] for item in snapshot['items']], ['new'])

    def test_partial_delete_restores_current_bytes_and_can_retry(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        remove = self.storage.remove_object

        def deny_metadata(bucket, key, version_id=None):
            if key.endswith('/old/metadata.json'):
                error = PermissionError('private')
                error.code = 'AccessDenied'
                raise error
            return remove(bucket, key, version_id)

        with self.client() as client:
            self.prepare_latest(client, latest)
            with patch.object(self.storage, 'remove_object', side_effect=deny_metadata):
                result = self.sync(client)
            self.assertEqual(result['failed'][0]['recoveryStatus'], 'restored-for-retry')
            self.assertIn(('templates', old['objectKey']), self.storage.objects)
            result = self.sync(client)
            self.assertEqual([item['publicationId'] for item in result['deleted']], ['old'])

    def test_duplicate_latest_with_mismatched_identity_never_replaces_valid_old(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        keeper = self.seed('keeper', template='other', version=3, created='2022-01-01T00:00:00Z')
        self.copy_package(keeper, latest)
        with self.client() as client:
            self.prepare_latest(client, keeper)
            result = self.sync(client)
            self.assertFalse(result['deleted'])
            self.assertIn(('templates', old['objectKey']), self.storage.objects)
            self.assertIn(('templates', latest['objectKey']), self.storage.objects)
            self.assertIn(('templates', keeper['objectKey']), self.storage.objects)

    def test_failed_history_delete_keeps_duplicate_latest(self):
        old = self.seed('old')
        latest = self.seed('new', version=2)
        keeper = self.seed('keeper', template='other', version=3, created='2022-01-01T00:00:00Z')
        self.copy_package(keeper, latest)
        remove = self.storage.remove_object

        def deny_old(bucket, key, version_id=None):
            if '/old/' in key:
                error = PermissionError('private')
                error.code = 'AccessDenied'
                raise error
            return remove(bucket, key, version_id)

        with self.client() as client:
            self.prepare_latest(client, keeper)
            with patch.object(self.storage, 'remove_object', side_effect=deny_old):
                result = self.sync(client)
            self.assertFalse(result['deleted'])
            self.assertIn(('templates', old['objectKey']), self.storage.objects)
            self.assertIn(('templates', latest['objectKey']), self.storage.objects)


if __name__ == '__main__':
    unittest.main()
