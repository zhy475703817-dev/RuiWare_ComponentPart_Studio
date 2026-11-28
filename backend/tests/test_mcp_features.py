"""Protocol-level coverage of editing, atomicity and publication tools."""
import unittest
from unittest.mock import patch

from backend.tests import test_mcp
from backend.tests.test_components import member
from backend.tests.storage_helpers import MemoryMinio
from backend.tests.test_library_cleanup import LibraryCleanupFixture
from backend.app.config import MinioSettings, Settings
from backend.app.main import create_app
from fastapi.testclient import TestClient
from pathlib import Path
import os


class MCPFeatureTests(unittest.TestCase):
    setUp = test_mcp.MCPTests.setUp
    client = test_mcp.MCPTests.client
    rpc = test_mcp.MCPTests.rpc
    call = test_mcp.MCPTests.call

    def edit(self, client, draft, tool, **arguments):
        result = self.call(client, tool, {'draftId': draft['draftId'],
            'expectedRevision': draft['document']['revision'], **arguments})
        self.assertFalse(result.get('isError'), result)
        return result['structuredContent']

    def test_editing_tools_share_revision_history_with_the_page(self):
        with self.client() as client:
            draft = self.call(client, 'component_create_draft', {})['structuredContent']
            for identifier in ('a', 'b'):
                draft = self.edit(client, draft, 'component_add_member', member=member(identifier))
            draft = self.edit(client, draft, 'component_add_slot', slot={
                'id': 'slot', 'name': '安装位', 'required': False, 'memberId': None})
            draft = self.edit(client, draft, 'component_update_slot', slotId='slot', patch={'name': '新安装位'})
            self.assertEqual(draft['document']['slots'][0]['name'], '新安装位')
            draft = self.edit(client, draft, 'component_remove_slot', slotId='slot')
            draft = self.edit(client, draft, 'component_merge_interface_types', types=[{'id': 'custom:安装面', 'label': '安装面'}])
            draft = self.edit(client, draft, 'component_set_interface_type', memberId='a', interfaceId='face.left', connectionTypeId='custom:安装面')
            draft = self.edit(client, draft, 'component_set_interface_connectable_types', memberId='a', interfaceId='face.left', connectableTypeIds=['custom:安装面'])
            self.assertEqual(draft['document']['assembly']['members'][0]['interfaces'][0]['connectableTypeIds'], ['custom:安装面'])
            draft = self.edit(client, draft, 'component_inherit_interface', memberId='a', interfaceId='face.left', name='外接口')
            port = draft['document']['ports'][0]['id']
            draft = self.edit(client, draft, 'component_rename_interface', portId=port, name='新外接口')
            self.assertEqual(draft['document']['ports'][0]['name'], '新外接口')
            draft = self.edit(client, draft, 'component_remove_interface', portId=port)
            draft = self.edit(client, draft, 'component_add_mate', fromMemberId='a', fromInterfaceId='face.left', toMemberId='b', toInterfaceId='face.left')
            draft = self.edit(client, draft, 'component_remove_mate', mateId=draft['document']['assembly']['mates'][0]['id'])
            draft = self.edit(client, draft, 'component_add_parameter', parameter={'id': 'count', 'label': '数量', 'value': 2, 'min': 1, 'max': 5, 'unit': '个'})
            draft = self.edit(client, draft, 'component_add_parameter', parameter={'id': 'spacing', 'label': '间距', 'value': 20, 'min': 0, 'max': 100, 'unit': 'mm'})
            draft = self.edit(client, draft, 'component_add_pattern', pattern={'id': 'array', 'label': '阵列', 'memberIds': ['a'], 'countParameterId': 'count', 'spacingParameterId': 'spacing', 'axis': 'x'})
            self.assertEqual(len(draft['document']['patterns']), 1)
            draft = self.edit(client, draft, 'component_remove_pattern', patternId='array')
            draft = self.edit(client, draft, 'component_remove_parameter', parameterId='count')
            draft = self.edit(client, draft, 'component_add_rule', rule={'id': 'new-rule', 'label': '说明', 'expression': '仅记录'})
            draft = self.edit(client, draft, 'component_remove_rule', ruleId='new-rule')
            current = client.get('/api/component-drafts/'+draft['draftId']).json()
            self.assertEqual(current['document'], draft['document'])
            self.assertTrue(all(change['actor'] == 'agent' for change in current['changes']))

    def test_batch_is_atomic_and_conflicts_never_overwrite_user_input(self):
        with self.client() as client:
            draft = self.call(client, 'component_create_draft', {})['structuredContent']
            args = {'draftId': draft['draftId'], 'expectedRevision': 1, 'actions': [
                {'type': 'definition.patch', 'patch': {'description': 'must roll back'}},
                {'type': 'member.patch', 'id': 'missing', 'patch': {'role': 'bad'}},
            ]}
            result = self.call(client, 'component_apply_actions', args)
            self.assertTrue(result.get('isError'), result)
            self.assertEqual(client.get('/api/component-drafts/'+draft['draftId']).json()['document'], draft['document'])
            args['actions'] = [{'type': 'member.add', 'member': member('a')}, {'type': 'member.add', 'member': member('b')}]
            added = self.call(client, 'component_apply_actions', args)
            self.assertFalse(added.get('isError'), added)
            self.assertEqual(added['structuredContent']['document']['revision'], 2)
            user = client.post('/api/component-drafts/'+draft['draftId']+'/actions', json={
                'expectedRevision': 2, 'action': {'type': 'definition.patch', 'patch': {'name': '用户值'}}})
            self.assertEqual(user.status_code, 200)
            stale = self.call(client, 'component_apply_actions', args)
            self.assertEqual(stale['structuredContent']['error']['code'], 'REVISION_CONFLICT')
            self.assertTrue(stale['isError'])
            self.assertEqual(client.get('/api/component-drafts/'+draft['draftId']).json()['document']['definition']['name'], '用户值')
            invalid = self.call(client, 'component_apply_actions', {**args, 'actions': []})
            self.assertTrue(invalid['isError'])

    def test_parameter_binding_uses_existing_validation_and_updates_members(self):
        with self.client() as client:
            draft = self.call(client, 'component_create_draft', {})['structuredContent']
            item = member('a')
            item['parameterDefinitions'] = [{'id': 'length', 'label': '长度', 'unit': 'mm', 'valueType': 'number', 'default': 2400, 'minimum': 100, 'maximum': 5000}]
            draft = self.edit(client, draft, 'component_add_member', member=item)
            draft = self.edit(client, draft, 'component_set_binding', bindingId='custom-binding', binding={
                'id': 'custom-binding', 'parameterId': 'componentWidth', 'memberId': 'a', 'memberParameterId': 'length'})
            draft = self.edit(client, draft, 'component_set_parameter', parameterId='componentWidth', value=800)
            self.assertEqual(draft['document']['assembly']['members'][0]['parameterValues']['length'], 800)
            draft = self.edit(client, draft, 'component_set_binding', bindingId='custom-binding', binding=None)
            self.assertFalse(any(binding['id'] == 'custom-binding' for binding in draft['document']['bindings']))

    def test_publication_search_details_and_download_share_rest_results(self):
        # This test isolates storage discovery; the real certificate/CAD
        # workflow is exercised without this stub in test_mcp_authoring_workflow.
        from backend.tests.storage_helpers import qualified_storage_fixture
        qualified_storage_fixture(self)
        settings = Settings(draft_db=Path(os.environ['COMPONENT_DRAFT_DB']), minio=MinioSettings(
            endpoint='minio.test', access_key='access', secret_key='secret', prefix='publishers/test'))
        storage = MemoryMinio()
        with patch('minio.Minio', return_value=storage), TestClient(create_app(settings, dist_dir=None),
            base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)) as client:
            draft = self.call(client, 'component_create_draft', {'name': '可复用组件'})['structuredContent']
            published = self.call(client, 'component_publish_draft', {'draftId': draft['draftId'], 'expectedRevision': 1})
            self.assertFalse(published.get('isError'), published)
            publication = published['structuredContent']
            found = self.call(client, 'component_search_templates', {'templateKind': 'component', 'query': '可复用'})
            self.assertEqual(found['structuredContent']['count'], 1)
            self.assertEqual(found['structuredContent']['items'][0]['publicationId'], publication['publicationId'])
            details = self.call(client, 'component_get_publication', {'publicationId': publication['publicationId'], 'sha256': publication['sha256']})
            self.assertFalse(details.get('isError'), details)
            self.assertEqual(details['structuredContent']['document'], draft['document'])
            self.assertTrue(details['structuredContent']['downloadUrl'].startswith('http://127.0.0.1:8020/'))
            self.assertEqual(client.get(details['structuredContent']['downloadUrl']).status_code, 200)
            wrong = self.call(client, 'component_get_publication', {'publicationId': publication['publicationId'], 'sha256': 'f'*64})
            self.assertTrue(wrong['isError'])
            self.assertEqual(self.call(client, 'component_list_publications', {})['structuredContent']['count'], 1)
            self.assertTrue(self.call(client, 'component_get_status', {})['structuredContent']['libraryConfigured'])
            parent = self.call(client, 'component_create_draft', {'name': '上层组件'})['structuredContent']
            nested = self.edit(client, parent, 'component_add_template_member', publicationId=publication['publicationId'],
                sha256=publication['sha256'], templateKind='component', position=[0, 0, 0])
            self.assertEqual(nested['document']['assembly']['members'][0]['template']['templateKind'], 'component')
            self.assertEqual(nested['document']['assembly']['members'][0]['parameterValues']['componentLength'], 2400)


class LibraryMaintenanceMCPTests(LibraryCleanupFixture, unittest.TestCase):
    rpc = test_mcp.MCPTests.rpc
    call = test_mcp.MCPTests.call

    def client(self):
        return TestClient(create_app(self.settings, dist_dir=None), base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345))

    def test_partial_cleanup_reports_deleted_and_rejected_items_on_both_transports(self):
        old = self.seed('old')
        self.seed('new', version=2)
        with self.client() as client:
            def selection():
                scan = client.post('/api/template-library/cleanup-preview').json()
                return {'scanId': scan['scanId'], 'publicationIds': ['old', 'unknown']}

            response = client.post('/api/template-library/cleanup', json=selection())
            self.assertEqual(response.status_code, 409)
            deleted, rejected = response.json()['items']
            self.assertEqual(deleted['status'], 'deleted')
            self.assertEqual(rejected, {'publicationId': 'unknown', 'status': 'rejected', 'errorCode': 'PUBLICATION_CHANGED'})
            self.assertEqual(client.post('/api/template-library/backups/' + deleted['backupId'] + '/restore').status_code, 200)

            result = self.call(client, 'component_cleanup_library', {'request': selection()})
            self.assertTrue(result.get('isError'), result)
            again, other = result['structuredContent']['items']
            self.assertEqual((again['publicationId'], again['status']), ('old', 'deleted'))
            self.assertEqual(other, rejected)
            self.assertNotIn(('templates', old['objectKey']), self.storage.objects)

    def test_cleanup_confirmation_backups_restore_and_errors_use_shared_service(self):
        old = self.seed('old')
        payload = self.storage.objects['templates', old['objectKey']]
        self.seed('new', version=2)
        with self.client() as client:
            missing = self.call(client, 'component_cleanup_library', {'request': {'scanId': '0'*32, 'publicationIds': ['old']}})
            self.assertTrue(missing.get('isError'), missing)
            self.assertEqual(missing['structuredContent']['error']['code'], 'CLEANUP_SCAN_NOT_FOUND')
            preview = self.call(client, 'component_preview_library_cleanup', {})
            self.assertFalse(preview.get('isError'), preview)
            cleaned = self.call(client, 'component_cleanup_library', {'request': {
                'scanId': preview['structuredContent']['scanId'], 'publicationIds': ['old']}})
            self.assertFalse(cleaned.get('isError'), cleaned)
            backup_id = cleaned['structuredContent']['items'][0]['backupId']
            self.assertNotIn(('templates', old['objectKey']), self.storage.objects)
            backups = self.call(client, 'component_list_backups', {})['structuredContent']['items']
            self.assertEqual(backups[0]['backupId'], backup_id)
            download = self.call(client, 'component_get_backup', {'backupId': backup_id})['structuredContent']
            self.assertEqual(client.get(download['downloadUrl']).content, payload)
            early_delete = self.call(client, 'component_delete_backup', {'backupId': backup_id})
            self.assertTrue(early_delete.get('isError'), early_delete)
            self.assertEqual(early_delete['structuredContent']['error']['code'], 'BACKUP_NOT_EXPIRED')
            restored = self.call(client, 'component_restore_backup', {'backupId': backup_id})
            self.assertFalse(restored.get('isError'), restored)
            self.assertIn(('templates', old['objectKey']), self.storage.objects)
            audit = self.call(client, 'component_get_cleanup_audit', {})['structuredContent']['items']
            self.assertTrue(any(item['status'] == 'restored' for item in audit))

    def test_catalog_and_reference_tools_do_not_start_work_when_only_reading(self):
        with self.client() as client:
            catalog = self.call(client, 'component_get_library', {})
            self.assertFalse(catalog.get('isError'), catalog)
            self.assertFalse(catalog['structuredContent']['syncing'])
            applied = self.call(client, 'component_apply_library', {})
            self.assertFalse(applied.get('isError'), applied)
            synced = self.call(client, 'component_sync_library', {})
            self.assertFalse(synced.get('isError'), synced)
            references = self.call(client, 'component_sync_references', {})
            self.assertFalse(references.get('isError'), references)
            status = self.call(client, 'component_get_references', {})
            self.assertFalse(status.get('isError'), status)
            tools = {item['name']: item for item in self.rpc(client, 'tools/list').json()['result']['tools']}
            self.assertTrue(tools['component_sync_library']['annotations']['destructiveHint'])
            self.assertTrue(tools['component_cleanup_library']['annotations']['destructiveHint'])
            self.assertTrue(tools['component_get_library']['annotations']['readOnlyHint'])


if __name__ == '__main__':
    unittest.main()
