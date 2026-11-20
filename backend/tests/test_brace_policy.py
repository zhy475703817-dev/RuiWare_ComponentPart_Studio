"""Prevent ambiguous AI-authored brace topology before expensive CAD solving."""
import copy
import unittest
from types import SimpleNamespace

import numpy as np

from backend.app.components.brace_generation import expand_brace_generation_rules, materialize_brace_generation_interfaces
from backend.app.components.brace_policy import valid_interface_pattern
from backend.app.components.models import ComponentDocument
from backend.app.components.resize_solver import ResizeFailure
from backend.app.components.service import validate_component, serialize_component
from backend.app.mcp.authoring import authoring_report
from backend.tests.test_geometry_resize import document, member


def shared_column_fixture():
    columns = [member('left'), member('right')]
    for column in columns:
        column['interfaces'] = [{'id': f'hole.{i:03d}', 'name': '侧孔', 'type': 'axis', 'tags': []}
                                for i in range(1, 9)]
    braces = [member(identifier) for identifier in ('h1', 'd1', 'h2', 'd2')]
    source = document([*columns, *braces])
    source['ports'][0]['source']['interfaceId'] = 'hole.001'
    source['parameters'] = [
        {'id': name, 'label': name, 'value': value, 'unit': 'mm', 'min': 0, 'max': 1000}
        for name, value in (('height', 400), ('pitch', 100), ('bottom', 0), ('top', 0))
    ]
    base = {'id': 'odd', 'label': '奇数孔列', 'heightParameterId': 'height', 'pitchParameterId': 'pitch',
            'bottomOffsetParameterId': 'bottom', 'topClearanceParameterId': 'top',
            'leftColumnMemberId': 'left', 'rightColumnMemberId': 'right',
            'horizontalMemberId': 'h1', 'diagonalMemberId': 'd1',
            'horizontalLeftInterfaceId': 'start', 'horizontalRightInterfaceId': 'end',
            'diagonalLeftInterfaceId': 'start', 'diagonalRightInterfaceId': 'end',
            'leftColumnInterfacePattern': 'hole.{index:03d}', 'rightColumnInterfacePattern': 'hole.{index:03d}',
            'columnInterfaceIndexStart': 1, 'columnInterfaceIndexStep': 2,
            'diagonalColumnInterfaceIndexStep': 2, 'connectionMode': 'geometry'}
    source['braceGenerationRules'] = [base, {**base, 'id': 'even', 'label': '偶数孔列',
                                           'horizontalMemberId': 'h2', 'diagonalMemberId': 'd2',
                                           'columnInterfaceIndexStart': 2}]
    return source


class BraceCompositionTests(unittest.TestCase):
    def test_disabled_rules_leave_existing_instances_and_connections_untouched(self):
        source = shared_column_fixture()
        expand_brace_generation_rules(source)
        for rule in source['braceGenerationRules']:
            rule['enabled'] = False
        source['parameters'][0]['value'] = 250
        before = copy.deepcopy(source)
        self.assertEqual(expand_brace_generation_rules(source), [])
        self.assertEqual(materialize_brace_generation_interfaces(source, None), {})
        self.assertEqual(source, before)
        from backend.app.components.qualification_policy import brace_height_grids, design_key
        self.assertEqual(brace_height_grids(source), [])
        enabled = copy.deepcopy(source)
        enabled['braceGenerationRules'][0]['enabled'] = True
        self.assertNotEqual(design_key(source), design_key(enabled))

    def test_raised_diagonal_start_reduces_count_and_preserves_clearance_on_rebuild(self):
        source = shared_column_fixture()
        source['braceGenerationRules'] = source['braceGenerationRules'][:1]
        source['braceGenerationRules'][0]['diagonalColumnInterfaceIndexStart'] = 2
        for height, diagonal_count in ((400, 1), (600, 2), (400, 1)):
            source['parameters'][0]['value'] = height
            relations = expand_brace_generation_rules(source)
            diagonals = [r for r in relations if '__diagonal__' in r['id']]
            self.assertEqual(len(diagonals), diagonal_count * 2)
            self.assertTrue(all((int(r['b']['interfaceId'].split('.')[-1])-1) * 100 <= height
                                for r in diagonals))
        source['parameters'][0]['value'] = 200
        with self.assertRaises(ResizeFailure) as caught:
            expand_brace_generation_rules(source)
        self.assertEqual(caught.exception.code, 'BRACE_RULE_TOO_SHORT')
        self.assertEqual(caught.exception.details, [{'minimumHeight': 300}])

    def test_distinct_diagonal_row_origin_uses_real_coordinates_not_index_difference(self):
        source = shared_column_fixture()
        source['braceGenerationRules'] = source['braceGenerationRules'][:1]
        source['braceGenerationRules'][0].update(
            diagonalLeftColumnInterfacePattern='diagonal.{index:03d}',
            diagonalRightColumnInterfacePattern='diagonal.{index:03d}')
        geometries = {}
        for member in source['assembly']['members']:
            frames = {}
            for index in range(1, 9):
                for prefix, offset in (('hole', 0), ('diagonal', 100)):
                    identifier = f'{prefix}.{index:03d}'
                    frames[identifier] = {'center': np.array([0., 0., offset+(index-1)*100]),
                                          'normal': np.array([1., 0., 0.])}
                    if prefix == 'diagonal':
                        member['interfaces'].append({'id': identifier})
            geometries[member['id']] = SimpleNamespace(frames=frames)
        relations = expand_brace_generation_rules(source, geometries)
        self.assertEqual(len([r for r in relations if '__diagonal__' in r['id']]), 2)

        # The second, alternating diagonal ends on the left column. An
        # asymmetric hole row must be checked on that actual destination.
        source['parameters'][0]['value'] = 600
        geometries['left'].frames['diagonal.005']['center'][2] = 700
        with self.assertRaises(ResizeFailure) as caught:
            expand_brace_generation_rules(source, geometries)
        self.assertEqual(caught.exception.code, 'BRACE_RULE_RANGE_INVALID')
        self.assertEqual(caught.exception.details, [{'memberId': 'left', 'interfaceId': 'diagonal.005'}])

    def test_shared_columns_and_separate_prototypes_rebuild_both_rules_without_accumulating(self):
        source = shared_column_fixture()
        self.assertEqual(next(c for c in validate_component(ComponentDocument.model_validate(source))
                              if c['id'] == 'brace.rules')['status'], 'pass')
        for height, count in ((400, 12), (600, 16), (200, 8), (400, 12)):
            source['parameters'][0]['value'] = height
            relations = expand_brace_generation_rules(source)
            self.assertEqual(len(source['assembly']['members']), count)
            self.assertEqual(len(relations), 2 * (count - 2))
            self.assertEqual(len({m['id'] for m in source['assembly']['members']}), count)
            before = copy.deepcopy(source)
            self.assertEqual(expand_brace_generation_rules(source), relations)
            self.assertEqual(source, before)
            for rule in ('odd', 'even'):
                targets = [int(r['b']['interfaceId'].split('.')[-1]) for r in relations if f'__brace__{rule}__' in r['id']]
                self.assertTrue(targets)
                self.assertTrue(all(i % 2 == (rule == 'odd') for i in targets))
            self.assertEqual({m['parameterValues']['length'] for m in source['assembly']['members']}, {100})

    def test_same_pair_of_real_ports_cannot_receive_duplicate_rods_even_with_distinct_prototypes(self):
        for reverse in (False, True):
            source = shared_column_fixture()
            second = source['braceGenerationRules'][1]
            second['columnInterfaceIndexStart'] = 1
            for item in source['assembly']['members']:
                if item['id'] in ('h2', 'd2'):
                    item['template']['templateKind'] = 'part'
                    item['parameterValues']['length'] = 100.0
            if reverse:
                second['leftColumnMemberId'], second['rightColumnMemberId'] = 'right', 'left'
                # A reversed asymmetric source can occupy different material;
                # sharing holes alone cannot prove that it is a duplicate rod.
                self.assertTrue(expand_brace_generation_rules(source))
                continue
            with self.subTest(reverse=reverse), self.assertRaises(ResizeFailure) as caught:
                expand_brace_generation_rules(source)
            self.assertEqual(caught.exception.code, 'BRACE_RULE_NODE_CONFLICT')
            self.assertEqual(caught.exception.details[0]['ruleId'], 'even')
            self.assertEqual(caught.exception.details[0]['otherRuleId'], 'odd')

        distinct = shared_column_fixture()
        distinct['braceGenerationRules'][1]['columnInterfaceIndexStart'] = 1
        for item in distinct['assembly']['members']:
            if item['id'] in ('h2', 'd2'):
                item['template']['publicationId'] = 'different-source'
        self.assertTrue(expand_brace_generation_rules(distinct))

    def test_prototype_ownership_is_checked_for_imports_and_before_any_expansion(self):
        for mode, expected in (('shared', 'BRACE_RULE_PROTOTYPE_CONFLICT'),
                               ('column', 'BRACE_RULE_MEMBER_ROLE_CONFLICT'),
                               ('generated', 'BRACE_RULE_GENERATED_PROTOTYPE'),
                               ('duplicate-id', 'BRACE_RULE_ID_CONFLICT')):
            source = shared_column_fixture()
            if mode == 'shared': source['braceGenerationRules'][1]['horizontalMemberId'] = 'h1'
            if mode == 'column': source['braceGenerationRules'][1]['leftColumnMemberId'] = 'h1'
            if mode == 'generated': source['assembly']['members'][2]['generatedBy'] = 'odd'
            if mode == 'duplicate-id': source['braceGenerationRules'][1]['id'] = 'odd'
            original = copy.deepcopy(source)
            with self.subTest(mode=mode), self.assertRaises(ResizeFailure) as caught:
                expand_brace_generation_rules(source)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(source, original)
            report = authoring_report({'document': source, 'checks': []})
            self.assertIn(expected, {issue['code'] for issue in report['issues']})
            self.assertEqual(source, original)

    def test_interface_patterns_require_one_bounded_number_token(self):
        for pattern in ('孔.{index}', 'holes.{index:03d}', 'feature.{index:10d}.left'):
            with self.subTest(pattern=pattern): self.assertTrue(valid_interface_pattern(pattern))
        for pattern in ('hole.001', 'hole.{index}.{index}', 'hole.{wrong}', 'hole.{index:999999d}', 'hole.{index}.{unknown}'):
            source = shared_column_fixture()
            source['braceGenerationRules'][0]['diagonalRightColumnInterfacePattern'] = pattern
            original = copy.deepcopy(source)
            with self.subTest(pattern=pattern), self.assertRaises(ResizeFailure) as caught:
                expand_brace_generation_rules(source)
            self.assertEqual(caught.exception.code, 'BRACE_RULE_PATTERN_INVALID')
            self.assertEqual(source, original)

    def test_parameter_units_and_target_range_fail_before_cad_or_expansion(self):
        from unittest.mock import Mock
        for changes, expected in (({'unit': 'cm'}, 'BRACE_RULE_UNIT_INVALID'),
                                  ({'value': 1200}, 'BRACE_RULE_PARAMETER_RANGE'),
                                  ({'value': -100}, 'BRACE_RULE_PARAMETER_RANGE')):
            source = shared_column_fixture()
            source['parameters'][0].update(changes)
            original = copy.deepcopy(source)
            provider = Mock()
            with self.subTest(changes=changes), self.assertRaises(ResizeFailure) as caught:
                materialize_brace_generation_interfaces(source, provider)
            self.assertEqual(caught.exception.code, expected)
            provider.evaluate.assert_not_called()
            self.assertEqual(source, original)

    def test_rule_name_prefix_does_not_own_a_foreign_connection(self):
        source = shared_column_fixture()
        foreign = {'id': '__brace__odd__foreign__horizontal__0__left',
                   'from': {'memberId': 'h1', 'interfaceId': 'start'},
                   'to': {'memberId': 'left', 'interfaceId': 'hole.001'}, 'relation': '贴合', 'offset': 0}
        source['assembly']['mates'].append(foreign)
        expand_brace_generation_rules(source)
        self.assertIn(foreign, source['assembly']['mates'])

    def test_export_keeps_generated_ownership_for_later_reconstruction_and_shrink(self):
        source = shared_column_fixture()
        expand_brace_generation_rules(source)
        serialized = serialize_component(ComponentDocument.model_validate(source))
        restored = copy.deepcopy(source)
        restored['assembly']['members'] = []
        for exported in serialized['stages']['assembly']['members']:
            instance = {key: value for key, value in exported.items()
                        if key not in ('memberId', 'partTemplateRef', 'partTemplateVersion', 'bindings')}
            instance.update(id=exported['memberId'], template=exported['partTemplateRef'])
            restored['assembly']['members'].append(instance)
            if exported['memberId'].startswith('generated-brace-'):
                self.assertIn(exported['generatedBy'], ('odd', 'even'))
                self.assertGreater(exported['generatedIndex'], 0)
            else:
                self.assertNotIn('generatedBy', exported)
        restored['parameters'][0]['value'] = 200
        expand_brace_generation_rules(restored)
        self.assertEqual(len(restored['assembly']['members']), 8)

    def test_mcp_reports_conflict_and_repair_guidance_without_changing_the_draft(self):
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient
        from backend.app.main import create_app
        from backend.app.config import Settings
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(draft_db=Path(directory)/'test.sqlite'), dist_dir=None)
            with TestClient(app, base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)) as client:
                app.state.qualification_jobs.debounce = 100
                source = shared_column_fixture()
                draft = app.state.drafts.create(document=ComponentDocument.model_validate(source))
                response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream', 'Host': '127.0.0.1:8020'}, json={
                    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                        'name': 'component_set_brace_generation_rule', 'arguments': {
                            'draftId': draft['draftId'], 'expectedRevision': draft['document']['revision'],
                            'rule': {**source['braceGenerationRules'][0], 'id': 'conflicting'},
                        },
                    },
                })
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()['result']
                self.assertTrue(result['isError'], result)
                error = result['structuredContent']['error']
                self.assertEqual(error['code'], 'BRACE_RULE_PROTOTYPE_CONFLICT')
                self.assertIn('独立成员', error['message'])
                self.assertEqual(error['details'][0]['otherRuleId'], 'odd')
                self.assertEqual(app.state.drafts.get(draft['draftId'])['document'], draft['document'])


if __name__ == '__main__': unittest.main()
