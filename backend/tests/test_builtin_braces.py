"""Column-frame authoring must finish the capability before users adjust height."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.components.brace_policy import built_in_brace_issues
from backend.app.components.models import ComponentDocument
from backend.app.components.service import validate_component
from backend.app.mcp.authoring import authoring_report, requirements
from backend.tests.test_geometry_resize import document, member
from backend.tests.test_brace_policy import shared_column_fixture
from backend.tests import test_qualification as qualification_helpers
from backend.tests import test_geometry_resize_cad as cad_helpers


def frame():
    source = shared_column_fixture()
    source['definition'].update(name='双立柱柱片', category='货架柱片')
    source['bindings'] = [
        {'id': 'height-' + identifier, 'parameterId': 'height',
         'memberId': identifier, 'memberParameterId': 'length'}
        for identifier in ('left', 'right')
    ]
    return source


class BuiltInBracePolicyTests(unittest.TestCase):
    def test_every_new_frame_requires_rules_without_a_separate_user_request(self):
        source = frame()
        source.pop('braceGenerationRules')
        original = copy.deepcopy(source)
        self.assertEqual(built_in_brace_issues(source)[0]['code'], 'AUTHORING_BRACE_RULE_REQUIRED')
        checks = validate_component(ComponentDocument.model_validate(source))
        self.assertEqual(next(c for c in checks if c['id'] == 'brace.builtin')['status'], 'fail')
        issues = authoring_report({'document': source, 'checks': checks})['issues']
        self.assertTrue(any(i['code'] == 'AUTHORING_BRACE_RULE_REQUIRED' and i['status'] == 'fail' for i in issues))
        self.assertEqual(source, original)
        self.assertTrue(any(r['stage'] == 'column-frame' for r in requirements()['rules']))

    def test_rule_presence_cannot_hide_unowned_legacy_braces_or_missing_height_binding(self):
        source = frame()
        self.assertEqual(built_in_brace_issues(source), [])
        extra = copy.deepcopy(source['assembly']['members'][2])
        extra.update(id='legacy-cross', name='横撑', role='横撑')
        source['assembly']['members'].append(extra)
        findings = built_in_brace_issues(source)
        self.assertEqual(findings[0]['code'], 'AUTHORING_BRACE_MEMBER_UNCOVERED')
        self.assertEqual(findings[0]['memberId'], 'legacy-cross')
        extra['generatedBy'] = 'odd'
        self.assertEqual(built_in_brace_issues(source), [])
        source['bindings'].pop()
        self.assertIn('AUTHORING_BRACE_HEIGHT_BINDING_REQUIRED', {i['code'] for i in built_in_brace_issues(source)})

    def test_switch_off_preserves_intrinsic_requirement_and_metadata_does_not_hide_structure(self):
        source = frame()
        for rule in source['braceGenerationRules']:
            rule['enabled'] = False
        self.assertEqual(built_in_brace_issues(source), [])
        source['definition'].update(name='任意名称', category='组件')
        for m in source['assembly']['members']:
            m['role'] = '立柱' if m['id'] in ('left', 'right') else '横撑' if m['id'].startswith('h') else '斜撑'
        source['braceGenerationRules'] = []
        self.assertEqual(built_in_brace_issues(source)[0]['code'], 'AUTHORING_BRACE_RULE_REQUIRED')
        self.assertEqual(built_in_brace_issues(document()), [])
        single = document([member('upright')])
        single['assembly']['members'][0]['role'] = '立柱'
        self.assertEqual(built_in_brace_issues(single), [])

    def test_nested_frame_defect_cannot_hide_behind_parent_geometry_evidence(self):
        child = frame()
        child['braceGenerationRules'] = []
        nested = member('child')
        nested['template']['templateKind'] = 'component'
        nested['instanceDocument'] = child
        parent = document([nested])
        findings = authoring_report({'document': parent, 'checks': []})['issues']
        issue = next(i for i in findings if i['code'] == 'AUTHORING_BRACE_RULE_REQUIRED')
        self.assertEqual(issue['path'], ['child'])
        self.assertEqual(issue['status'], 'fail')


class BuiltInBracePublishTests(unittest.TestCase):
    setUp = qualification_helpers.QualificationTests.setUp
    create = qualification_helpers.QualificationTests.create
    wait = qualification_helpers.QualificationTests.wait

    def test_missing_builtin_rule_is_rejected_before_cad_and_cannot_publish(self):
        source = document()
        source['definition'].update(name='柱片', category='货架柱片')
        created = self.create(source)
        result = self.wait()
        self.assertFalse(result['deliveryReady'])
        self.assertEqual(result['error']['code'], 'AUTHORING_DATA_INVALID')
        self.assertTrue(any(c['id'] == 'brace.builtin' for c in result['error']['details']))
        self.assertEqual(self.client.get(self.base).json()['document'], created['document'])
        self.assertEqual(self.client.post(self.base + '/publish', json={'expectedRevision': 1}).status_code, 409)


class BuiltInBraceCadTests(unittest.TestCase):
    def test_creation_qualifies_builtin_capability_even_when_usage_switch_is_off(self):
        from backend.app.config import Settings
        from backend.app.main import create_app
        from backend.app.components.resize_geometry import CadProvider
        from backend.app.components.resize_solver import ResizeSolver
        fixture = json.loads((Path(__file__).parent / 'fixtures/geometry-resize-v1.json').read_text(encoding='utf8'))
        provider = CadProvider({(t['reference']['publicationId'], t['reference']['sha256']): (t['draft'], t['material'])
                                for t in fixture['templates'].values()})
        source = cad_helpers.RealCadResizeTests.brace_source(SimpleNamespace(fixture=fixture))
        source['braceGenerationRules'][0].update(columnInterfaceIndexStep=7, remainderPolicy='reject')
        source = ResizeSolver(source, provider, {}, {}).solve()['document']
        source['braceGenerationRules'][0]['enabled'] = False
        source['revision'] = 1
        before = copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(draft_db=Path(directory) / 'test.sqlite'), dist_dir=None)
            with patch('backend.app.components.qualification.load_geometry', new=AsyncMock(return_value=(provider, {}))), \
                 TestClient(app) as client:
                app.state.qualification_jobs.debounce = .3
                created = client.post('/api/component-drafts', json={'document': source}).json()
                base = '/api/component-drafts/' + created['draftId']
                # Even a request to skip general adjustment axes cannot skip
                # the frame's mandatory automatic-brace capability checks.
                response = client.put(base + '/qualification/config', json={'expectedRevision': 1, 'config': {'axes': []}})
                self.assertEqual(response.status_code, 200, response.text)
                deadline = time.monotonic() + 480
                while time.monotonic() < deadline:
                    report = client.get(base + '/qualification').json()
                    if report['status'] not in ('queued', 'pending', 'running', 'stale'):
                        break
                    time.sleep(.15)
                self.assertTrue(report['deliveryReady'], report)
                cases = [c for c in report['cases'] if c['label'].startswith('内置补齐')]
                self.assertEqual(len(cases), 3)
                self.assertTrue(all(c['status'] == 'passed' for c in cases))
                self.assertLess(cases[1]['memberCount'], cases[0]['memberCount'])
                self.assertGreater(cases[2]['memberCount'], cases[0]['memberCount'])
                saved = client.get(base).json()['document']
                self.assertFalse(saved['braceGenerationRules'][0]['enabled'])
                self.assertEqual(len(saved['assembly']['members']), len(before['assembly']['members']))
                self.assertEqual([p['id'] for p in saved['parameters']], [p['id'] for p in before['parameters']])
                for actual, expected in zip(saved['parameters'], before['parameters']):
                    self.assertAlmostEqual(actual['value'], expected['value'], delta=1e-6)
                    self.assertEqual({k: v for k, v in actual.items() if k != 'value'},
                                     {k: v for k, v in expected.items() if k != 'value'})
                self.assertEqual(saved['ports'], before['ports'])
                self.assertAlmostEqual(saved['geometryDesign']['dimensions'][2], 2400, delta=.1)
                self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
