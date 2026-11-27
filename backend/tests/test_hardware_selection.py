import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.app.components.hardware_selection import audit_hardware_selection, INPUT_PRESETS
from backend.app.components.models import ComponentAction, ComponentDocument, InterfaceHardware
from backend.app.components.repository import DraftRepository
from backend.app.components.service import ComponentError, create_component_document


def document():
    data = create_component_document().model_dump(by_alias=True)
    data['assembly']['members'] = [{
        'id': id, 'name': id, 'code': 'PART', 'role': '',
        'template': {'publicationId': 'p', 'templateId': 't', 'version': 1, 'sha256': 'a' * 64},
        'position': [0, 0, 0], 'rotation': [0, 0, 0], 'parameterValues': {},
        'interfaces': [{'id': 'i', 'name': '连接端', 'type': 'plane', 'tags': []}],
    } for id in ['a', 'b']]
    data['assembly']['mates'] = [{'id': 'ab', 'from': {'memberId': 'a', 'interfaceId': 'i'},
                                 'to': {'memberId': 'b', 'interfaceId': 'i'}, 'relation': '贴合', 'offset': 0}]
    data['ports'] = [{'id': 'p', 'name': '对外端', 'source': {'memberId': 'a', 'interfaceId': 'i'}, 'tags': []}]
    return ComponentDocument.model_validate(data)


class HardwareSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = DraftRepository(Path(self.temp.name) / 'drafts.sqlite3')
        self.draft = self.repository.create(document=document())

    def save(self, hardware):
        self.draft = self.repository.update(self.draft['draftId'], self.draft['document']['revision'],
            ComponentAction.validate_python({'type': 'interface.hardware.set', 'memberId': 'a', 'interfaceId': 'i', 'hardware': hardware}))
        return self.draft

    def test_custom_dimensions_save_readback_and_port_snapshot(self):
        self.assertFalse(INPUT_PRESETS['interfaceDimensionChecks'])
        hardware = {'mode': 'manual', 'scenarioId': 'bolt-into-thread', 'note': '用户尺寸', 'items': [
            {'category': '螺栓', 'name': '六角螺栓', 'specification': 'M7×0.75 × 37.5 mm / 自定材料', 'quantity': 2}]}
        saved = self.save(hardware)
        self.assertTrue(saved['hardwareSelection']['complete'])
        reread = self.repository.get(saved['draftId'])
        self.assertEqual(reread['document']['assembly']['members'][0]['interfaces'][0]['hardware'], hardware)
        self.assertEqual(reread['document']['ports'][0]['interface']['hardware'], hardware)

    def test_pending_configuration_saves_but_cannot_publish(self):
        self.save({'mode': 'manual', 'scenarioId': 'bolt-into-thread', 'items': [
            {'category': '螺栓', 'name': '六角螺栓', 'specification': '', 'quantity': 1}]})
        self.assertEqual(self.draft['hardwareSelection']['pending'], 1)
        with self.assertRaises(ComponentError) as context:
            self.repository.prepare_publication(self.draft['draftId'], self.draft['document']['revision'])
        self.assertEqual(context.exception.code, 'HARDWARE_SELECTION_REQUIRED')
        self.assertEqual(len(context.exception.details), 1)
        self.assertFalse(self.repository.qualification(self.draft['draftId'])['deliveryReady'])

    def test_connected_requirement_precedes_even_a_passing_geometry_certificate(self):
        with patch.object(self.repository, '_qualification', return_value={'deliveryReady': True}):
            with self.assertRaises(ComponentError) as context:
                self.repository.prepare_publication(self.draft['draftId'], self.draft['document']['revision'])
        self.assertEqual(context.exception.code, 'HARDWARE_SELECTION_REQUIRED')
        self.save({'mode': 'none', 'items': []})
        self.assertFalse(self.draft['hardwareSelection']['complete'])

    def test_unconnected_reserved_interfaces_do_not_require_hardware(self):
        data = document().model_dump(by_alias=True)
        data['assembly']['mates'] = []
        data['assembly']['members'][0]['interfaces'][0]['required'] = True
        self.assertTrue(audit_hardware_selection(data)['complete'])
        self.assertEqual(audit_hardware_selection(data)['total'], 0)
        data['geometryDesign'] = {'relations': [{'a': {'path': ['a'], 'interfaceId': 'i'},
                                                'b': {'path': ['b'], 'interfaceId': 'i'}}]}
        self.assertEqual(audit_hardware_selection(data)['pending'], 1)

    def test_complete_configuration_still_requires_geometry_qualification(self):
        self.save({'mode': 'none', 'scenarioId': 'weld-only', 'note': '此处焊接，不增加独立五金', 'items': []})
        self.assertTrue(self.draft['hardwareSelection']['complete'])
        with self.assertRaises(ComponentError) as context:
            self.repository.prepare_publication(self.draft['draftId'], self.draft['document']['revision'])
        self.assertEqual(context.exception.code, 'AUTHORING_QUALIFICATION_REQUIRED')

    def test_legacy_shape_is_preserved_and_pending(self):
        legacy = {'mode': 'none', 'items': []}
        self.assertEqual(InterfaceHardware.model_validate(legacy).model_dump(), legacy)
        self.assertEqual(self.save(legacy)['hardwareSelection']['pending'], 1)

    def test_mcp_exposes_rules_presets_and_native_setter(self):
        import asyncio
        from types import SimpleNamespace
        from mcp.server.fastmcp import FastMCP
        from backend.app.mcp.component_tools import register
        mcp = FastMCP('hardware-test')
        app = SimpleNamespace(state=SimpleNamespace(drafts=self.repository, resize_jobs=SimpleNamespace(latest_history=lambda _: None)))
        async def invoke(fn): return fn()
        register(mcp, app, invoke)
        names = [tool.name for tool in asyncio.run(mcp.list_tools())]
        for name in ['component_get_hardware_selection_rules', 'component_get_hardware_input_presets', 'component_set_interface_hardware']:
            self.assertIn(name, names)

    def test_geometry_verified_authoring_retains_hardware_pending(self):
        from backend.app.mcp.authoring import authoring_report
        data = self.draft['document']
        data['geometryDesign'] = {'verified': True}
        history = {'expectedRevision': 1, 'appliedRevision': data['revision'], 'result': {'checksum': 'match'}}
        with patch('backend.app.mcp.authoring.fingerprint', return_value='match'):
            report = authoring_report({**self.draft, 'document': data}, history)
        self.assertTrue(report['geometryVerified'])
        self.assertEqual(report['hardwareSelection']['pending'], 1)
        self.assertTrue(any(issue['code'].startswith('HARDWARE_') for issue in report['issues']))


if __name__ == '__main__':
    unittest.main()
