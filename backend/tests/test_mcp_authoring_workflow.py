"""An Agent's full MCP workflow with real CAD, certificates and immutable releases.

Only S3 is replaced by an in-memory object store. No fake solver or certificate.
"""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient

from backend.app.config import MinioSettings, Settings
from backend.app.main import create_app
from backend.tests.storage_helpers import MemoryMinio


class MCPAuthoringWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.storage = MemoryMinio()
        self.enterContext(patch('minio.Minio', return_value=self.storage))
        self.settings = Settings(draft_db=Path(directory)/'drafts.sqlite', minio=MinioSettings(
            endpoint='minio.test', access_key='test', secret_key='test', prefix='publishers/test'))
        fixture = json.loads((Path(__file__).parent/'fixtures/geometry-resize-v1.json').read_text(encoding='utf-8'))['templates']['resize-fixture-2']
        draft = fixture['draft']
        documents = {
            'manifest.json': draft,
            'parameters.json': {'definitions': draft['parameterDefinitions']},
            'geometry-recipe.json': draft['geometryRecipe'],
            'feature-rules.json': {'rules': draft['featureRules'], 'reviewed': draft['featureRulesReviewed']},
            'constraints.json': draft['sketch'],
            'sweep-path.json': draft['sweepPath'] or {'status': 'empty'},
            'interfaces.json': {'coordinateSystem': draft['coordinateSystem'], 'interfaces': draft['interfaces']},
            'variants.json': {'variants': draft['variants']},
            'material-requirements.json': {'requirements': draft['materialRequirements'], 'blank': draft['blank']},
            'material-validation.json': {'samples': draft['materialValidationSamples'], 'resolved': [
                {'sampleId': 'material.nominal', 'material': fixture['material']['record']}]},
            'evidence.json': {'items': [], 'aiProposals': []},
            'admission.json': {'policy': draft['admission']},
        }
        stream = BytesIO()
        with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
            for name, value in documents.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False))
        payload = stream.getvalue()
        self.part = {'publicationId': 'real-brace', 'templateId': draft['id'], 'version': 1,
            'name': draft['name'], 'code': draft['code'], 'createdAt': '2026-11-27T00:00:00Z',
            'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload),
            'objectKey': 'publishers/test/real-brace/template.rwpart'}
        self.storage.objects[self.settings.minio.bucket, self.part['objectKey']] = payload
        self.storage.objects[self.settings.minio.bucket, 'publishers/test/real-brace/metadata.json'] = json.dumps(self.part).encode()
        self.app = create_app(self.settings, dist_dir=None)
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)))
        self.app.state.qualification_jobs.debounce = 60

    def rpc(self, method, params=None):
        response = self.client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['result']

    def call(self, tool, **arguments):
        result = self.rpc('tools/call', {'name': tool, 'arguments': arguments})
        self.assertFalse(result.get('isError'), result)
        return result['structuredContent']

    def edit(self, draft, tool, **arguments):
        result = self.call(tool, draftId=draft['draftId'], expectedRevision=draft['document']['revision'], **arguments)
        self.assertIn('authoring', result, 'Every draft-editing tool must return current authoring guidance')
        self.assertEqual(result['authoring']['revision'], result['document']['revision'])
        return result

    def finish(self, draft):
        self.call('component_cancel_authoring', draftId=draft['draftId'])
        self.call('component_finish_authoring', draftId=draft['draftId'], expectedRevision=draft['document']['revision'], retry=True)
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            result = self.call('component_authoring_status', draftId=draft['draftId'])
            if result['status'] not in ('pending', 'queued', 'running', 'stale'):
                self.assertTrue(result['deliveryReady'], result)
                return self.call('component_get_draft', draftId=draft['draftId']), result
            time.sleep(.1)
        self.fail('MCP authoring did not reach a terminal state')

    def test_agent_can_inspect_current_parameters_without_a_packaged_stl(self):
        info = self.call('component_get_template_authoring_info', publicationId=self.part['publicationId'],
            sha256=self.part['sha256'], parameters={'length': 1000})
        self.assertEqual(info['publication']['sha256'], self.part['sha256'])
        self.assertTrue(info['resolvedInterfacesAvailable'])
        self.assertTrue(info['resolvedInterfaces'])
        self.assertTrue(any(item['id'] == 'length' for item in info['parameters']))
        result = self.rpc('tools/call', {'name': 'component_get_template_authoring_info', 'arguments': {
            'publicationId': self.part['publicationId'], 'sha256': self.part['sha256'], 'parameters': {'length': -1}}})
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['error']['code'], 'PARAMETER_INVALID')

    def test_mcp_only_creation_qualification_publication_and_nested_reuse(self):
        status = self.call('component_get_status')
        self.assertTrue(status['libraryConfigured'])
        self.assertTrue(status['publicationStorageConfigured'])
        found = self.call('component_search_templates', query=self.part['code'])
        self.assertEqual(found['items'][0]['publicationId'], self.part['publicationId'])
        self.call('component_get_authoring_requirements')
        info = self.call('component_get_template_authoring_info', publicationId=self.part['publicationId'], sha256=self.part['sha256'])
        draft = self.call('component_create_draft', name='Agent workflow component')
        premature = self.rpc('tools/call', {'name': 'component_publish_draft', 'arguments': {
            'draftId': draft['draftId'], 'expectedRevision': draft['document']['revision']}})
        self.assertTrue(premature['isError'])
        self.assertEqual(premature['structuredContent']['error']['code'], 'AUTHORING_QUALIFICATION_REQUIRED')
        # New drafts contain illustrative rack dimensions. Replace those with
        # a driver from the selected part's actual public parameter contract.
        length = next(item for item in info['parameters'] if item['id'] == 'length')
        draft = self.edit(draft, 'component_apply_actions', actions=[
            *[{'type': 'parameter.remove', 'id': item['id']} for item in draft['document']['parameters']],
            {'type': 'parameter.add', 'parameter': {'id': 'memberSpan', 'label': '杆件尺寸', 'unit': length['unit'],
                'value': length['default'], 'min': length['minimum'], 'max': length['maximum']}},
        ])
        draft = self.edit(draft, 'component_add_template_member', publicationId=self.part['publicationId'], sha256=self.part['sha256'])
        member = draft['document']['assembly']['members'][0]
        draft = self.edit(draft, 'component_set_binding', bindingId='span', binding={
            'id': 'span', 'parameterId': 'memberSpan', 'memberId': member['id'], 'memberParameterId': 'length'})
        draft = self.edit(draft, 'component_inherit_interface', memberId=member['id'], interfaceId=member['interfaces'][0]['id'], name='mount')
        content = b'Original component design requirements'
        attachment = {'id': 'drawing', 'fileName': 'requirements.txt', 'mediaType': 'text/plain', 'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'dataUrl': 'data:text/plain;base64,'+base64.b64encode(content).decode()}
        draft = self.edit(draft, 'component_update_definition', patch={'evidenceFiles': [attachment]})
        self.call('component_configure_authoring', draftId=draft['draftId'], expectedRevision=draft['document']['revision'], config={'axes': [2]})
        draft, certificate = self.finish(draft)
        self.assertTrue(all(case['status'] == 'passed' for case in certificate['cases']))
        self.assertGreaterEqual(len(certificate['cases']), 3)
        released = self.call('component_publish_draft', draftId=draft['draftId'], expectedRevision=draft['document']['revision'])
        self.assertTrue(released['qualification']['deliveryReady'])
        self.assertEqual(released['qualification']['revision'], released['document']['revision'])
        self.assertEqual(released['readiness']['templateStatus'], 'qualified')
        self.assertEqual(released['readiness']['engineeringStatus'], 'incomplete')
        package = self.client.get(released['downloadUrl']).content
        self.assertEqual(hashlib.sha256(package).hexdigest(), released['sha256'])
        with ZipFile(BytesIO(package)) as archive:
            self.assertTrue(json.loads(archive.read('qualification.json'))['deliveryReady'])
            evidence = json.loads(archive.read('evidence.json'))
            self.assertEqual(base64.b64decode(evidence[0]['dataUrl'].split(',', 1)[1]), content)
        detail = self.call('component_get_publication', publicationId=released['publicationId'], sha256=released['sha256'])
        self.assertEqual(detail['qualification'], released['qualification'])
        self.assertEqual(detail['evidenceFiles'][0]['sha256'], attachment['sha256'])
        # The same immutable artifact must reject altered attachment bytes,
        # and cannot claim a certificate for different document contents.
        from backend.app.components.publications import ComponentPublications
        from backend.app.components.service import ComponentError
        with ZipFile(BytesIO(package)) as archive:
            documents = {name: json.loads(archive.read(name)) for name in archive.namelist()}
        def repack(values):
            stream = BytesIO()
            with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                for name, value in values.items():
                    archive.writestr(name, json.dumps(value))
            return stream.getvalue()
        documents['evidence.json'][0]['dataUrl'] = 'data:text/plain;base64,'+base64.b64encode(b'changed').decode()
        with self.assertRaises(ComponentError) as rejected:
            ComponentPublications._parse_package(repack(documents), released)
        self.assertEqual(rejected.exception.code, 'REMOTE_STORAGE_INTEGRITY_ERROR')
        documents.pop('evidence.json')
        legacy = ComponentPublications._parse_package(repack(documents), released)
        self.assertFalse(legacy['readiness']['attachmentsComplete'])
        self.assertEqual(legacy['readiness']['templateStatus'], 'unverified')
        documents['component.json']['definition']['description'] = 'Changed after qualification'
        altered = ComponentPublications._parse_package(repack(documents), released)
        self.assertEqual(altered['readiness']['templateStatus'], 'unverified')
        parent = self.call('component_create_draft', name='Reusable parent')
        parent = self.edit(parent, 'component_apply_actions', actions=[
            *[{'type': 'parameter.remove', 'id': item['id']} for item in parent['document']['parameters']],
            {'type': 'parameter.add', 'parameter': {'id': 'parentSpan', 'label': '上层杆件尺寸', 'unit': 'mm',
                'value': 1200, 'min': length['minimum'], 'max': length['maximum']}},
        ])
        parent = self.edit(parent, 'component_add_template_member', publicationId=released['publicationId'], sha256=released['sha256'], templateKind='component')
        nested = parent['document']['assembly']['members'][0]
        parent = self.edit(parent, 'component_inherit_interface', memberId=nested['id'], interfaceId=nested['interfaces'][0]['id'], name='parent mount')
        self.call('component_configure_authoring', draftId=parent['draftId'], expectedRevision=parent['document']['revision'], config={'axes': [2]})
        parent, parent_certificate = self.finish(parent)
        self.assertTrue(parent_certificate['deliveryReady'])
        self.assertTrue(parent['document']['assembly']['members'][0]['instanceDocument'])
        second_release = self.call('component_publish_draft', draftId=parent['draftId'], expectedRevision=parent['document']['revision'])
        self.assertTrue(second_release['qualification']['deliveryReady'])
        self.assertEqual(self.call('component_search_templates', templateKind='component')['count'], 2)

    def test_multi_member_template_keeps_declared_real_interface_relation(self):
        draft = self.call('component_create_draft', name='Paired braces')
        draft = self.edit(draft, 'component_apply_actions', actions=[
            *[{'type': 'parameter.remove', 'id': item['id']} for item in draft['document']['parameters']],
            {'type': 'parameter.add', 'parameter': {'id': 'span', 'label': 'Span', 'value': 1200, 'unit': 'mm', 'min': 500, 'max': 2000}},
        ])
        draft = self.edit(draft, 'component_add_template_member', publicationId=self.part['publicationId'], sha256=self.part['sha256'])
        draft = self.edit(draft, 'component_copy_member', memberId=draft['document']['assembly']['members'][0]['id'])
        a, b = draft['document']['assembly']['members']
        endpoint = a['interfaces'][0]['id']
        draft = self.edit(draft, 'component_inherit_interface', memberId=a['id'], interfaceId=endpoint, name='mount')
        job = self.call('component_resize_preview', draftId=draft['draftId'], request={
            'expectedRevision': draft['document']['revision'], 'design': {'locks': [], 'relations': [{
                'id': 'declared-pair-offset', 'a': {'path': [a['id']], 'interfaceId': endpoint},
                'b': {'path': [b['id']], 'interfaceId': endpoint}, 'mode': 'preserve-offset',
                'origin': 'user', 'offset': [40, 0, 0]}]}})
        deadline = time.monotonic()+60
        while job['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.1)
            job = self.call('component_resize_status', draftId=draft['draftId'], jobId=job['jobId'])
        self.assertEqual(job['status'], 'ready', job)
        draft = self.call('component_resize_apply', draftId=draft['draftId'], request={
            'expectedRevision': draft['document']['revision'], 'jobId': job['jobId'], 'checksum': job['result']['checksum']})
        self.assertFalse(draft['hardwareSelection']['complete'])
        # This fixture preserves spacing between separate members, without a fastened joint.
        draft = self.edit(draft, 'component_set_interface_hardware', memberId=a['id'], interfaceId=endpoint,
            hardware={'mode': 'none', 'scenarioId': 'custom-none', 'items': [],
                'note': 'Geometry-only fixture: the members remain 40 mm apart with no physical fastening.'})
        self.assertTrue(draft['hardwareSelection']['complete'])
        self.call('component_configure_authoring', draftId=draft['draftId'], expectedRevision=draft['document']['revision'],
            config={'axes': [2]})
        draft, report = self.finish(draft)
        self.assertEqual(len(draft['document']['assembly']['members']), 2)
        self.assertTrue(report['deliveryReady'])
        self.assertTrue(any(r['id'] == 'declared-pair-offset' for r in draft['document']['geometryDesign']['relations']))
        release = self.call('component_publish_draft', draftId=draft['draftId'], expectedRevision=draft['document']['revision'])
        self.assertEqual(release['readiness']['templateStatus'], 'qualified')


if __name__ == '__main__':
    unittest.main()
