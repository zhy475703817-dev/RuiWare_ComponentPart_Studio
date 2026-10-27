import hashlib
import io
import asyncio
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi.testclient import TestClient

from backend.app.config import MinioSettings, Settings
from backend.app.main import create_app
from backend.engine.template_core import TemplateDraft
from backend.engine.template_core.models import MaterialValidationSample
from backend.tests.storage_helpers import MemoryMinio
from backend.tests.test_preview import STL, metadata


def reconstructable_package(valid=True):
    draft = TemplateDraft(id='draft-1', code='BEAM-1', name='缓存横梁')
    draft.materialValidationSamples = [MaterialValidationSample.model_validate({'id': 'material.nominal', 'role': 'nominal', 'name': 'steel', 'bindingId': 'mat-1',
                                       'bindingMode': 'copy', 'materialCode': 'M1', 'materialName': 'steel',
                                       'materialThickness': 2, 'requiredForAdmission': True})]
    documents = {
        'manifest.json': draft.model_dump(mode='json'),
        'parameters.json': {'definitions': [p.model_dump() for p in draft.parameterDefinitions]},
        'geometry-recipe.json': draft.geometryRecipe.model_dump() if valid else {'operations': []},
        'feature-rules.json': {'reviewed': True, 'rules': []},
        'constraints.json': draft.sketch.model_dump(),
        'sweep-path.json': {'status': 'empty'},
        'interfaces.json': {'coordinateSystem': draft.coordinateSystem, 'interfaces': []},
        'variants.json': {'variants': []},
        'material-requirements.json': {'requirements': [], 'blank': draft.blank.model_dump()},
        'material-validation.json': {'samples': [item.model_dump() for item in draft.materialValidationSamples], 'resolved': [{
            'sampleId': 'material.nominal', 'material': {'id': '1', 'code': 'M1', 'name': 'steel', 'type': 'raw', 'thickness': 2},
        }]},
        'evidence.json': {'items': []}, 'admission.json': {'policy': {}},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, document in documents.items():
            archive.writestr(name, json.dumps(document))
    return stream.getvalue()


class LibraryCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(api_url='http://parts.test', api_key='test-secret',
                                 draft_db=Path(self.tmp.name) / 'components.sqlite3',
                                 minio=MinioSettings(endpoint='minio.test', access_key='test', secret_key='test', prefix='publishers/test'))
        self.storage = MemoryMinio()
        self.minio = patch('minio.Minio', return_value=self.storage).start()
        self.worker = patch('backend.app.reconstruction._run_worker', return_value=STL).start()
        self.addCleanup(patch.stopall)
        self.payloads = {'version-1': reconstructable_package()}
        self.records = [metadata(self.payloads['version-1'])]
        self.part_keys = set()
        self.calls = []
        self.offline = False
        self.fail_download = False
        self.fail_ids = set()

    def _write_part_records(self):
        """Mirror the current published part catalog into the shared bucket."""
        for key in self.part_keys:
            self.storage.objects.pop((self.settings.minio.bucket, key), None)
            self.storage.fail_get.discard(key)
        self.part_keys.clear()
        for record in self.records:
            identifier = record['publicationId']
            object_key = f"{self.settings.minio.prefix}/{identifier}/template.rwpart"
            metadata_record = {**record, 'templateKind': 'part', 'objectKey': object_key}
            metadata_key = object_key.removesuffix('/template.rwpart') + '/metadata.json'
            payload = self.payloads[identifier]
            for key, content in ((object_key, payload), (metadata_key, json.dumps(metadata_record).encode())):
                self.storage.put_object(self.settings.minio.bucket, key, io.BytesIO(content), len(content))
                self.part_keys.add(key)
            if self.fail_download or identifier in self.fail_ids:
                self.storage.fail_get.add(object_key)

    def test_parts_load_from_minio_when_parts_platform_is_unavailable(self):
        self.offline = True

        with self.client() as client:
            body = self.settled(client)
            self.assertEqual([item['publicationId'] for item in body['items']], ['version-1'])
            self.assertEqual(body['errors'], [])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.worker.call_count, 1)

    def test_cache_sync_uses_legacy_http_only_when_minio_is_not_configured(self):
        settings = Settings(api_url='http://parts.test', api_key='test-secret',
                            draft_db=Path(self.tmp.name) / 'http-cache.sqlite3',
                            minio=MinioSettings())
        self.offline = False

        with self.client(settings) as client:
            body = self.settled(client)
            self.assertEqual([item['publicationId'] for item in body['items']], ['version-1'])
            self.assertEqual(self.calls, ['/api/public/v1/templates', '/api/public/v1/templates/version-1/download'])

    def upstream(self, request):
        self.calls.append(request.url.path)
        if self.offline:
            raise httpx.ConnectError('private server information', request=request)
        if request.url.path == '/api/public/v1/templates':
            return httpx.Response(200, json={'items': self.records, 'count': len(self.records)})
        identifier = request.url.path.split('/')[5]
        if request.url.path.endswith('/download'):
            if self.fail_download:
                return httpx.Response(503, json={'error': {'code': 'REMOTE_STORAGE_UNAVAILABLE'}})
            return httpx.Response(200, content=self.payloads[identifier])
        return httpx.Response(200, json=next(r for r in self.records if r['publicationId'] == identifier))

    def client(self, settings=None):
        return TestClient(create_app(settings or self.settings, transport=httpx.MockTransport(self.upstream), dist_dir=None))

    def settled(self, client, refresh=False):
        self._write_part_records()
        if refresh:
            response = client.post('/api/template-library/sync')
        else:
            response = client.get('/api/template-library')
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(300):
            body = client.get('/api/template-library').json()
            if not body['syncing']:
                return body
            time.sleep(.01)
        self.fail('background synchronization did not finish')

    def test_restart_reads_saved_catalog_package_and_model_without_upstream(self):
        with self.client() as client:
            body = self.settled(client)
            self.assertEqual(body['count'], 1, body)
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(self.calls, [])
        self.offline = True
        with self.client() as client:
            first = client.get('/api/template-library').json()
            self.assertEqual(first['count'], 1)
            body = self.settled(client)
            self.assertEqual(body['errors'], [])
            before = len(self.calls)
            digest = self.records[0]['sha256']
            prefix = '/api/published-templates/version-1'
            info = client.get(f'{prefix}/reconstruction?sha256={digest}')
            self.assertEqual(info.status_code, 200, info.text)
            model = client.post(f'{prefix}/reconstruction/model?sha256={digest}', json={'parameters': {}})
            self.assertEqual(model.status_code, 200, model.text[:100])
            self.assertEqual(model.content, STL)
            self.assertEqual(len(self.calls), before)
            self.assertEqual(self.worker.call_count, 1)
            self.assertEqual(client.get(f'{prefix}/reconstruction?sha256={"0" * 64}').status_code, 409)

    def test_new_and_deleted_templates_require_apply_and_unchanged_models_are_reused(self):
        with self.client() as client:
            self.settled(client)
            self.payloads['version-2'] = self.payloads['version-1']
            self.records.append({**self.records[0], 'publicationId': 'version-2', 'version': 2})
            body = self.settled(client, refresh=True)
            self.assertEqual(body['count'], 1)
            self.assertEqual(body['updateCount'], 1)
            applied = client.post('/api/template-library/apply').json()
            self.assertEqual(applied['count'], 2)
            self.assertEqual(applied['updateCount'], 0)
            self.records = []
            body = self.settled(client, refresh=True)
            self.assertEqual(body['count'], 2)
            self.assertEqual(body['updateCount'], 2)
            self.assertEqual(client.post('/api/template-library/apply').json()['count'], 0)
        package_key = f"{self.settings.minio.prefix}/version-1/template.rwpart"
        self.assertEqual(self.storage.reads.count(package_key), 1)

    def test_invalid_templates_stay_hidden_and_transient_failure_can_retry(self):
        self.payloads['version-1'] = reconstructable_package(False)
        self.records = [metadata(self.payloads['version-1'])]
        with self.client() as client:
            self.assertEqual(self.settled(client)['count'], 0)
            self.assertEqual(self.worker.call_count, 0)
            self.payloads['version-1'] = reconstructable_package()
            self.records = [metadata(self.payloads['version-1'])]
            self.fail_download = True
            body = self.settled(client, refresh=True)
            self.assertEqual(body['updateCount'], 0)
            self.assertIn('REMOTE_STORAGE_UNAVAILABLE', body['errors'])
            self.fail_download = False
            body = self.settled(client, refresh=True)
            self.assertEqual(body['updateCount'], 1)
            self.assertEqual(client.post('/api/template-library/apply').json()['count'], 1)

    def test_components_are_cached_but_publishing_still_checks_remote_dependencies(self):
        with self.client() as client:
            draft = client.post('/api/component-drafts', json={'name': '缓存组件'}).json()
            published = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={'expectedRevision': 1}).json()
            self.assertEqual(self.settled(client)['count'], 2)
        self.offline = True
        with self.client() as client:
            self.settled(client)
            with patch('backend.app.components.publications.ComponentPublications._metadata', side_effect=RuntimeError('offline')):
                detail = client.get('/api/published-components/' + published['publicationId'])
                self.assertEqual(detail.status_code, 200, detail.text)
                payload = client.get(published['downloadUrl'])
                self.assertEqual(hashlib.sha256(payload.content).hexdigest(), published['sha256'])
                retry = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={'expectedRevision': 1})
                self.assertEqual(retry.status_code, 503)

    def test_engine_change_rechecks_using_local_package_and_source_change_has_no_cache(self):
        from dataclasses import replace
        with self.client() as client:
            self.assertEqual(self.settled(client)['count'], 1)
        package_key = f"{self.settings.minio.prefix}/version-1/template.rwpart"
        downloads = self.storage.reads.count(package_key)
        with patch('backend.app.library_cache.engine_version', return_value='new-engine'):
            with self.client() as client:
                self.assertEqual(self.settled(client)['count'], 1)
        self.assertEqual(self.worker.call_count, 2)
        self.assertEqual(self.storage.reads.count(package_key), downloads)
        self.offline = True
        with self.client(replace(self.settings, api_key='another-user')) as client:
            self.assertEqual(self.settled(client)['count'], 1)

    def test_cached_model_never_bypasses_package_identity_and_metadata_refresh(self):
        with self.client() as client:
            self.settled(client)
            self.records[0]['name'] = '更新后的名称'
            self.assertEqual(self.settled(client, refresh=True)['updateCount'], 1)
            client.post('/api/template-library/apply')
            digest = self.records[0]['sha256']
            info = client.get(f'/api/published-templates/version-1/reconstruction?sha256={digest}')
            self.assertEqual(info.json()['publication']['name'], '更新后的名称')
            self.records[0]['templateId'] = 'wrong-draft'
            body = self.settled(client, refresh=True)
            self.assertEqual(body['count'], 1, 'unapplied changes must preserve the active catalog')
            self.assertEqual(body['updateCount'], 1, 'invalid identity must be excluded from pending catalog')
            self.assertEqual(client.post('/api/template-library/apply').json()['count'], 0)
        self.assertEqual(self.worker.call_count, 1)
        package_key = f"{self.settings.minio.prefix}/version-1/template.rwpart"
        self.assertEqual(self.storage.reads.count(package_key), 1)

    def test_invalid_identity_does_not_poison_another_publication_with_same_bytes(self):
        self.payloads['invalid-id'] = self.payloads['version-1']
        self.records.insert(0, {**self.records[0], 'publicationId': 'invalid-id', 'templateId': 'wrong-draft'})
        with self.client() as client:
            body = self.settled(client)
            self.assertEqual([item['publicationId'] for item in body['items']], ['version-1'])
        self.assertEqual(self.worker.call_count, 1)

    def test_components_validate_cached_bytes_against_current_metadata(self):
        with self.client() as client:
            draft = client.post('/api/component-drafts', json={'name': '缓存组件'}).json()
            published = client.post(f"/api/component-drafts/{draft['draftId']}/publish", json={'expectedRevision': 1}).json()
            self.settled(client)
            record = client.app.state.published_storage.records('component')[0]
            metadata_key = record.object_key.rsplit('/', 1)[0] + '/metadata.json'
            storage_key = (self.settings.minio.bucket, metadata_key)
            current = json.loads(self.storage.objects[storage_key])
            current['version'] += 1
            self.storage.objects[storage_key] = json.dumps(current).encode()
            body = self.settled(client, refresh=True)
            self.assertIn('REMOTE_STORAGE_INTEGRITY_ERROR', body['errors'])
            self.assertEqual(body['updateCount'], 0)
            current['version'] -= 1
            current['name'] = '更新后的组件名称'
            self.storage.objects[storage_key] = json.dumps(current, ensure_ascii=False).encode()
            self.settled(client, refresh=True)
            client.post('/api/template-library/apply')
            detail = client.get('/api/published-components/' + published['publicationId'])
            self.assertEqual(detail.json()['name'], '更新后的组件名称')

    def test_corrupt_cache_is_repaired_without_losing_drafts(self):
        import sqlite3
        with self.client() as client:
            self.settled(client)
            draft = client.post('/api/component-drafts', json={'name': '保留草稿'}).json()
            with closing(sqlite3.connect(self.settings.draft_db)) as db, db:
                db.execute("UPDATE library_packages SET payload=?", (b'corrupt',))
                db.execute("UPDATE library_models SET model=?", (b'corrupt',))
            body = self.settled(client, refresh=True)
            self.assertEqual(body['errors'], [])
            self.assertEqual(client.get('/api/component-drafts/' + draft['draftId']).status_code, 200)
            self.assertEqual(self.worker.call_count, 2)
            package_key = f"{self.settings.minio.prefix}/version-1/template.rwpart"
            self.assertEqual(self.storage.reads.count(package_key), 2)

    def test_force_check_during_sync_queues_one_additional_run(self):
        from backend.app.library_sync import LibrarySync
        from types import SimpleNamespace

        async def scenario():
            sync = LibrarySync(SimpleNamespace(state=SimpleNamespace(library_cache=None)))
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []

            async def parts():
                calls.append('parts')
                entered.set()
                await release.wait()

            async def components():
                calls.append('components')

            sync.sync_parts, sync.sync_components = parts, components
            sync.start()
            await entered.wait()
            sync.start(force=True)
            sync.start(force=True)
            release.set()
            await sync.task
            self.assertEqual(calls.count('components'), 2)

        asyncio.run(scenario())

    def test_failed_package_does_not_block_healthy_later_templates(self):
        self.payloads['version-2'] = self.payloads['version-1']
        self.records.append({**self.records[0], 'publicationId': 'version-2', 'version': 2})
        self.fail_ids.add('version-1')
        with TestClient(create_app(self.settings, transport=httpx.MockTransport(self.upstream), dist_dir=None)) as client:
            body = self.settled(client)
            self.assertEqual([item['publicationId'] for item in body['items']], ['version-2'])
            self.assertFalse(body['initialized'])
            self.assertIn('REMOTE_STORAGE_UNAVAILABLE', body['errors'])


if __name__ == '__main__':
    unittest.main()
