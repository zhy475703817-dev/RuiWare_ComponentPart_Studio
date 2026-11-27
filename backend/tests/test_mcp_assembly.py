import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests import test_mcp
from backend.tests.test_geometry_resize import BoxProvider, document, member


class AssemblyBoxProvider(BoxProvider):
    def evaluate(self, reference, parameters):
        shape = super().evaluate(reference, parameters)
        for interface in shape.interfaces:
            interface.update(referenceFrame={'axis': 'z'}, mating={
                'version': 1, 'family': 'plane-contact', 'role': 'contact-face',
                'dimensions': {}, 'allowedRelations': ['贴合', '重合'], 'capacity': 1})
        return shape


class MCPAssemblyTests(unittest.TestCase):
    rpc = test_mcp.MCPTests.rpc
    call = test_mcp.MCPTests.call

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(Settings(draft_db=Path(directory)/'drafts.sqlite'), dist_dir=None)
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)))
        self.publication = SimpleNamespace(publicationId='box', templateId='box', version=1, sha256='a'*64, name='Box', code='BOX')
        self.read_template = self.enterContext(patch('backend.app.reconstruction._read_template', new=AsyncMock(return_value=(self.publication, {}, {}))))

    def draft(self, members=None):
        value = document(members)
        for item in value['assembly']['members']:
            for interface in item['interfaces']:
                interface.update(referenceFrame={'axis': 'z'}, mating={
                    'version': 1, 'family': 'plane-contact', 'role': 'contact-face',
                    'dimensions': {}, 'allowedRelations': ['贴合', '重合'], 'capacity': 1})
        return self.call(self.client, 'component_create_draft', {'document': value})['structuredContent']

    def edit(self, draft, tool, **arguments):
        result = self.call(self.client, tool, {'draftId': draft['draftId'],
            'expectedRevision': draft['document']['revision'], **arguments})
        self.assertFalse(result.get('isError'), result)
        return result['structuredContent']

    def test_copy_keeps_independent_member_and_ground_uses_rotated_actual_shape(self):
        draft = self.draft([member('a', 30)])
        copied = self.edit(draft, 'component_copy_member', memberId='a')
        a, b = copied['document']['assembly']['members']
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(b['position'], [40, 0, 30])
        self.assertEqual(a['parameterValues'], b['parameterValues'])
        rotated = self.edit(copied, 'component_update_member', memberId='a', patch={'rotation': [0, np.pi/2, 0]})
        with patch('backend.app.components.assembly.CadProvider', return_value=BoxProvider()):
            grounded = self.edit(rotated, 'component_ground_member', memberId='a')
        grounded_a = grounded['document']['assembly']['members'][0]
        self.assertAlmostEqual(grounded_a['position'][2], 10, places=5)
        self.assertEqual(grounded['changes'][-1]['actor'], 'agent')

    def test_connect_faces_applies_alignment_and_mate_in_one_revision(self):
        draft = self.draft([member('a'), member('b', 180)])
        with patch('backend.app.components.assembly.CadProvider', return_value=AssemblyBoxProvider()):
            connected = self.edit(draft, 'component_connect_interfaces',
                fromMemberId='a', fromInterfaceId='end', toMemberId='b', toInterfaceId='start')
        self.assertEqual(connected['document']['revision'], 2)
        self.assertEqual(len(connected['document']['assembly']['mates']), 1)
        self.assertAlmostEqual(connected['document']['assembly']['members'][1]['position'][2], 100, places=5)
        # Occupied or incompatible endpoints may not leave a partial transform behind.
        baseline = copy.deepcopy(connected['document'])
        with patch('backend.app.components.assembly.CadProvider', return_value=AssemblyBoxProvider()):
            failed = self.call(self.client, 'component_connect_interfaces', {'draftId': draft['draftId'],
                'expectedRevision': 2, 'fromMemberId': 'a', 'fromInterfaceId': 'end', 'toMemberId': 'b', 'toInterfaceId': 'start'})
        self.assertTrue(failed['isError'])
        self.assertEqual(self.client.get('/api/component-drafts/'+draft['draftId']).json()['document'], baseline)

    def test_add_published_part_resolves_interfaces_and_places_it_without_overlap(self):
        draft = self.draft()
        definitions = [{'id': 'length', 'default': 100}]
        with patch('backend.app.components.assembly.CadProvider', return_value=BoxProvider()), patch('backend.app.reconstruction.editable_parameters', return_value=definitions):
            added = self.edit(draft, 'component_add_template_member', publicationId='box', sha256='a'*64)
        self.assertEqual(added['document']['revision'], 2)
        a, b = added['document']['assembly']['members']
        self.assertEqual(b['template']['publicationId'], 'box')
        self.assertEqual(b['parameterValues']['length'], 100)
        self.assertEqual(b['position'], [30, -5, 0])
        self.assertEqual([item['id'] for item in b['interfaces']], ['start', 'end'])
        self.assertEqual(added['changes'][-1]['actor'], 'agent')

    def test_real_cad_member_import_keeps_enum_parameters_and_resolved_interfaces(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/geometry-resize-v1.json').read_text(encoding='utf-8'))['templates']['resize-fixture-2']
        self.read_template.return_value = (self.publication, fixture['draft'], fixture['material'])
        draft = self.call(self.client, 'component_create_draft', {})['structuredContent']
        added = self.edit(draft, 'component_add_template_member', publicationId='box', sha256='a'*64)
        imported = added['document']['assembly']['members'][0]
        self.assertEqual(imported['parameterValues']['length'], 1200)
        self.assertEqual(imported['parameterValues']['sectionType'], '矩管')
        self.assertTrue(imported['resolvedInterfaces'])
        self.assertTrue(imported['interfaces'])
        self.assertEqual(imported['position'][2], 0)

    def test_page_edit_during_cad_computation_prevents_stale_assembly_write(self):
        draft = self.draft([member('a', 30)])
        provider = BoxProvider()
        evaluate = provider.evaluate
        def edit_then_measure(reference, parameters):
            self.app.state.drafts.update(draft['draftId'], 1, {'type': 'definition.patch', 'patch': {'name': '计算期间的用户修改'}})
            return evaluate(reference, parameters)
        with patch('backend.app.components.assembly.CadProvider', return_value=provider), patch.object(provider, 'evaluate', side_effect=edit_then_measure):
            result = self.call(self.client, 'component_ground_member', {'draftId': draft['draftId'], 'expectedRevision': 1, 'memberId': 'a'})
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['error']['code'], 'REVISION_CONFLICT')
        current = self.client.get('/api/component-drafts/'+draft['draftId']).json()['document']
        self.assertEqual(current['definition']['name'], '计算期间的用户修改')
        self.assertEqual(current['assembly']['members'][0]['position'], [0, 0, 30])

    def test_rebuilt_interface_contract_is_rechecked_before_connecting(self):
        draft = self.draft([member('a'), member('b', 180)])
        provider = AssemblyBoxProvider()
        evaluate = provider.evaluate
        def changed_contract(reference, parameters):
            shape = evaluate(reference, parameters)
            for interface in shape.interfaces:
                interface['mating']['allowedRelations'] = ['重合']
            return shape
        with patch('backend.app.components.assembly.CadProvider', return_value=provider), patch.object(provider, 'evaluate', side_effect=changed_contract):
            result = self.call(self.client, 'component_connect_interfaces', {'draftId': draft['draftId'], 'expectedRevision': 1,
                'fromMemberId': 'a', 'fromInterfaceId': 'end', 'toMemberId': 'b', 'toInterfaceId': 'start', 'relation': '贴合'})
        self.assertTrue(result.get('isError'), result)
        self.assertEqual(self.client.get('/api/component-drafts/'+draft['draftId']).json()['document'], draft['document'])

    def test_copy_does_not_rebind_or_change_source_parameter_values(self):
        value = document([member('a')])
        value['parameters'][0]['value'] = 1500
        original = value['assembly']['members'][0]
        original['parameterValues']['length'] = 1200
        original['parameterDefinitions'] = [{'id': 'length', 'label': '长度', 'unit': 'mm',
            'valueType': 'number', 'default': 1200, 'minimum': 100, 'maximum': 5000}]
        draft = self.call(self.client, 'component_create_draft', {'document': value})['structuredContent']
        copied = self.edit(draft, 'component_copy_member', memberId='a')
        self.assertEqual([item['parameterValues']['length'] for item in copied['document']['assembly']['members']], [1200, 1200])
        self.assertEqual(copied['document']['bindings'], [])
        self.assertEqual(copied['document']['parameters'][0]['value'], 1500)


if __name__ == '__main__':
    unittest.main()
