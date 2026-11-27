"""Published-shape regression fixtures: same engine path as production resize.

Fixtures contain six fixed template definitions (upright, cross brace, diagonal,
rectangular tube, end plate and angle), with no remote credentials or mutable DB.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from backend.app.components.models import ComponentDocument
from backend.app.components.resize_geometry import CadProvider, matrix
from backend.app.components.resize_solver import ResizeSolver, ResizeFailure
from backend.app.components.service import validate_component


class RealCadResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent/'fixtures/geometry-resize-v1.json').read_text(encoding='utf-8'))
        cls.provider = CadProvider({(t['reference']['publicationId'], t['reference']['sha256']): (t['draft'], t['material']) for t in cls.fixture['templates'].values()})

    def resize(self, kind, axis, delta):
        source = copy.deepcopy(self.fixture['components'][kind])
        before = copy.deepcopy(source)
        info = ResizeSolver(source, self.provider, {}, {'inspectOnly': True}).solve()
        dimensions = list(info['dimensions']); dimensions[axis] += delta
        result = ResizeSolver(source, self.provider, {}, {'dimensions': dimensions}).solve()
        self.assertTrue(np.allclose(result['dimensions'], dimensions, atol=.1))
        self.assertEqual(source, before)
        checked = ComponentDocument.model_validate(result['document'])
        # This legacy fixture predates built-in brace rules. Its geometry can
        # still be inspected/solved, but a column frame cannot be delivered
        # until its author adds the mandatory capability.
        failures = [c for c in validate_component(checked) if c['status'] == 'fail']
        self.assertEqual([c['id'] for c in failures], ['brace.builtin'] if kind == 'upright' else [])
        self.assertTrue(checked.geometryDesign.verified)
        self.assertTrue(checked.geometryDesign.interfaceSnapshots)
        return before, result

    def test_beam_includes_endplates_and_preserves_hole_diameter(self):
        source, result = self.resize('beam', 0, 100)
        self.assertGreater(len(result['relations']), 0)
        for old, new in zip(source['assembly']['members'], result['document']['assembly']['members']):
            if 'holeDiameter' in old['parameterValues']:
                self.assertEqual(old['parameterValues']['holeDiameter'], new['parameterValues']['holeDiameter'])

    def test_shelf_preserves_real_support_contacts(self):
        _, result = self.resize('shelf', 0, 100)
        self.assertTrue(any(r['id'].startswith('contact-') for r in result['relations']))

    def test_upright_frame_width_and_diagonals(self):
        source, result = self.resize('upright', 0, 100)
        old = {m['id']: m for m in source['assembly']['members']}
        members = {m['id']: m for m in result['document']['assembly']['members']}
        self.assertGreater(members['cross-1']['parameterValues']['length'], old['cross-1']['parameterValues']['length'])
        self.assertNotAlmostEqual(members['diagonal-1']['rotation'][1], old['diagonal-1']['rotation'][1], places=3)
        self.assertAlmostEqual(members['upright-left']['position'][2], 0, delta=.05)
        self.assertAlmostEqual(members['upright-right']['position'][2], 0, delta=.05)

    def test_upright_frame_height(self):
        source, result = self.resize('upright', 2, 100)
        members = {m['id']: m for m in result['document']['assembly']['members']}
        self.assertAlmostEqual(members['upright-left']['parameterValues']['length'], 2500, delta=.1)
        old_ids = {i['id'] for i in source['assembly']['members'][0]['interfaces']}
        new_ids = {i['id'] for i in members['upright-left']['interfaces']}
        self.assertTrue(old_ids <= new_ids)

    def brace_source(self):
        source = copy.deepcopy(self.fixture['components']['upright'])
        source['assembly']['members'] = [
            member for member in source['assembly']['members']
            if member['id'] in {'upright-left', 'upright-right', 'cross-1', 'diagonal-1'}
        ]
        # Preserve real static exports and valid bindings so this also covers
        # the production qualification gate, beyond solving geometry alone.
        member_ids = {member['id'] for member in source['assembly']['members']}
        source['bindings'] = [binding for binding in source['bindings'] if binding['memberId'] in member_ids]
        source['ports'] = [port for port in source['ports']
                           if port['source']['memberId'] in member_ids
                           and port['source']['interfaceId'] == 'upright.basePlane']
        source['parameters'].extend([
            {'id': 'bracePitch', 'label': '侧孔基础孔距', 'value': 75, 'unit': 'mm', 'min': 75, 'max': 75},
            {'id': 'diagonalPitch', 'label': '斜撑节点间距', 'value': 75, 'unit': 'mm', 'min': 75, 'max': 75},
            {'id': 'braceBottom', 'label': '底部孔位余量', 'value': 100, 'unit': 'mm', 'min': 100, 'max': 100},
            {'id': 'braceTop', 'label': '顶部孔位余量', 'value': 200, 'unit': 'mm', 'min': 200, 'max': 200},
        ])
        source['braceGenerationRules'] = [{
            'id': 'cad-upright-braces', 'label': '立柱真实孔位撑杆',
            'heightParameterId': 'frameElevationZ', 'pitchParameterId': 'bracePitch',
            'bottomOffsetParameterId': 'braceBottom', 'topClearanceParameterId': 'braceTop',
            'leftColumnMemberId': 'upright-left', 'rightColumnMemberId': 'upright-right',
            'horizontalMemberId': 'cross-1', 'diagonalMemberId': 'diagonal-1',
            'horizontalLeftInterfaceId': 'crossBrace.leftNode.part_face_front.001',
            'horizontalRightInterfaceId': 'crossBrace.rightNode.part_face_front.001',
            'diagonalLeftInterfaceId': 'diagonalBrace.startNode.part_face_front.001',
            'diagonalRightInterfaceId': 'diagonalBrace.endNode.part_face_front.001',
            'leftColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_left.{index:03d}',
            'rightColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_right.{index:03d}',
            'diagonalLeftColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_left.{index:03d}',
            'diagonalRightColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_right.{index:03d}',
            'diagonalPitchParameterId': 'diagonalPitch',
            'columnInterfaceIndexStart': 1, 'columnInterfaceIndexStep': 2,
            'diagonalColumnInterfaceIndexStep': 7,
            'remainderPolicy': 'reject', 'connectionMode': 'geometry',
        }]
        return source

    def test_real_upright_hole_rows_expand_fixed_braces_and_rebuild_after_shrink(self):
        source = self.brace_source()
        measured = ResizeSolver(source, self.provider, {}, {
            'inspectOnly': True, 'dimensions': [1216, 114, 3000],
        }).solve()
        self.assertAlmostEqual(measured['dimensions'][2], 2400, delta=.1)
        grown_result = ResizeSolver(source, self.provider, {}, {'dimensions': [1216, 114, 3000]}).solve()
        self.assertTrue(np.allclose(grown_result['dimensions'], [1216, 114, 3000], atol=.1))
        grown = grown_result['document']
        grown_members = {item['id']: item for item in grown['assembly']['members']}
        grown_holes = [f'upright.sideAttachmentConnections.part_face_left.{index:03d}' for index in range(1, 37, 2)]
        self.assertTrue(set(grown_holes) <= {item['id'] for item in grown_members['upright-left']['interfaces']})
        self.assertAlmostEqual(grown_members['upright-left']['parameterValues']['length'], 3000)

        generated = [item for item in grown['assembly']['members'] if item.get('generatedBy') == 'cad-upright-braces']
        horizontal = [item for item in [*grown['assembly']['members']] if item['id'] == 'cross-1' or item.get('generatedBy') == 'cad-upright-braces' and item['id'].startswith('generated-brace-cad-upright-braces-cross-1-')]
        diagonal = [item for item in [*grown['assembly']['members']] if item['id'] == 'diagonal-1' or item.get('generatedBy') == 'cad-upright-braces' and item['id'].startswith('generated-brace-cad-upright-braces-diagonal-1-')]
        self.assertEqual(len(generated), 22)
        self.assertEqual(len(grown_result['structureChanges']['added']), 22)
        self.assertEqual(grown_result['structureChanges']['removed'], [])
        self.assertEqual(len(horizontal), 19)
        self.assertEqual(len(diagonal), 5)
        self.assertEqual({item['parameterValues']['length'] for item in horizontal}, {1200})
        self.assertEqual({item['parameterValues']['length'] for item in diagonal}, {1500})
        horizontal_relations = [relation for relation in grown['geometryDesign']['relations']
                                if relation['id'].startswith('__brace__cad-upright-braces__horizontal__')]
        self.assertEqual(len(horizontal_relations), len(horizontal) * 2)
        self.assertTrue(all('sideAttachmentConnections.part_face_' in relation['b']['interfaceId']
                            for relation in horizontal_relations))
        self.assertEqual(grown['assembly']['mates'], [])
        self.assertFalse([c for c in validate_component(ComponentDocument.model_validate(grown)) if c['status'] == 'fail'])

        # Real port centers must coincide, including alternating diagonal rods.
        geometry_by_member = {
            item['id']: self.provider.evaluate(item['template'], item['parameterValues'])
            for item in grown['assembly']['members']
        }
        for relation in grown['geometryDesign']['relations']:
            if not relation['id'].startswith('__brace__cad-upright-braces__'):
                continue
            positions = []
            for side in ('a', 'b'):
                endpoint = relation[side]
                member_id = endpoint['path'][0]
                frame = geometry_by_member[member_id].frames[endpoint['interfaceId']]
                member = grown_members[member_id]
                transform = matrix(member['position'], member['rotation'])
                positions.append(transform[:3, :3] @ frame['center'] + transform[:3, 3])
            self.assertLessEqual(np.linalg.norm(positions[1] - positions[0]), .05, relation['id'])

        for lock in ({'path': ['upright-left']}, {'path': ['upright-right'], 'parameterId': 'length'},
                     {'path': ['generated-brace-cad-upright-braces-cross-1-18']}):
            with self.subTest(lock=lock), self.assertRaises(ResizeFailure) as error:
                ResizeSolver(grown, self.provider, {}, {
                    'dimensions': [1216, 114, 1950], 'design': {'locks': [lock]},
                }).solve()
            self.assertEqual(error.exception.code, 'RESIZE_LOCK_CONFLICT')
        shortened_result = ResizeSolver(grown, self.provider, {}, {
            'dimensions': [1216, 114, 1950], 'design': {
                'locks': [{'path': ['cross-1']}, {'path': ['diagonal-1'], 'parameterId': 'length'}],
                'relations': grown['geometryDesign']['relations'],
            },
        }).solve()
        self.assertTrue(np.allclose(shortened_result['dimensions'], [1216, 114, 1950], atol=.1))
        shortened = shortened_result['document']
        shortened_members = shortened['assembly']['members']
        shortened_generated = [item for item in shortened_members if item.get('generatedBy') == 'cad-upright-braces']
        self.assertEqual(len(shortened_generated), 13)
        self.assertEqual(shortened_result['structureChanges']['added'], [])
        self.assertEqual(len(shortened_result['structureChanges']['removed']), 9)
        locked_cross = next(item for item in shortened_members if item['id'] == 'cross-1')
        for field in ('parameterValues', 'position', 'rotation'):
            self.assertEqual(locked_cross[field], grown_members['cross-1'][field])
        self.assertEqual(len({item['id'] for item in shortened_members}), len(shortened_members))
        self.assertEqual(sum(relation['id'].startswith('__brace__cad-upright-braces__') for relation in shortened['geometryDesign']['relations']), 30)
        self.assertFalse([c for c in validate_component(ComponentDocument.model_validate(shortened)) if c['status'] == 'fail'])
        shortened_left = next(item for item in shortened_members if item['id'] == 'upright-left')
        self.assertAlmostEqual(shortened_left['parameterValues']['length'], 1950)
        shortened_left_holes = {item['id'] for item in shortened_left['interfaces']
                                if 'sideAttachmentConnections.part_face_left.' in item['id']}
        expected_shortened_holes = {
            f'upright.sideAttachmentConnections.part_face_left.{index:03d}' for index in range(1, 25)
        }
        self.assertEqual(shortened_left_holes, expected_shortened_holes)
        shortened_horizontal_targets = {
            relation['b']['interfaceId'] for relation in shortened['geometryDesign']['relations']
            if relation['id'].startswith('__brace__cad-upright-braces__horizontal__')
            and relation['id'].endswith('__left')
        }
        self.assertEqual(shortened_horizontal_targets, set(grown_holes[:12]))

        # Automatically inferred contacts can be rebuilt after removing a rod;
        # an explicit user constraint to that rod must instead block shrinking.
        constrained = copy.deepcopy(grown)
        constrained['geometryDesign']['relations'].append({
            'id': '__brace__cad-upright-braces__horizontal__999__left', 'mode': 'preserve-offset', 'origin': 'user', 'offset': [0, 0, 0],
            'a': {'path': ['generated-brace-cad-upright-braces-cross-1-18'],
                  'interfaceId': 'crossBrace.leftNode.part_face_front.001'},
            'b': {'path': ['upright-left'], 'interfaceId': 'upright.basePlane'},
        })
        with self.assertRaises(ResizeFailure) as error:
            ResizeSolver(constrained, self.provider, {}, {'dimensions': [1216, 114, 1950]}).solve()
        self.assertEqual(error.exception.code, 'RESIZE_INTERFACE_MISSING')
        self.assertTrue(any(r['id'] == '__brace__cad-upright-braces__horizontal__999__left' for r in constrained['geometryDesign']['relations']))

    def test_raised_real_diagonal_start_and_legal_grid_continuation_after_growth(self):
        from backend.app.components.resize_jobs import solve_with_recovery
        source = self.brace_source()
        source['braceGenerationRules'][0]['diagonalColumnInterfaceIndexStart'] = 3
        grown = ResizeSolver(source, self.provider, {}, {'dimensions': [1216, 114, 3000]}).solve()['document']
        self.assertEqual(sum('__diagonal__' in r['id'] for r in grown['geometryDesign']['relations']), 8)
        before = copy.deepcopy(grown)
        solve = ResizeSolver.solve
        requests = []
        def needs_continuation(solver):
            target = solver.request.get('dimensions')
            if target:
                requests.append(target[2])
                height = next(p['value'] for p in solver.original_document['parameters'] if p['id'] == 'frameElevationZ')
                if abs(height - target[2]) > 600:
                    raise ResizeFailure('需要分步求解', 'RESIZE_NO_SOLUTION')
            return solve(solver)
        with patch('backend.app.components.resize_jobs.ResizeSolver.solve', needs_continuation):
            result = solve_with_recovery(grown, self.provider, {}, {'dimensions': [1216, 114, 1950]},
                                         lambda: False, lambda _: None)
        self.assertEqual(requests, [1950, 2400, 1950])
        self.assertTrue(result['automaticRecovery'])
        self.assertAlmostEqual(result['originalDimensions'][2], 3000, delta=.1)
        self.assertAlmostEqual(result['dimensions'][2], 1950, delta=.1)
        shortened = result['document']
        self.assertEqual(sum('__diagonal__' in r['id'] for r in shortened['geometryDesign']['relations']), 4)
        self.assertEqual(len(result['structureChanges']['removed']), 9)
        members = {m['id']: m for m in shortened['assembly']['members']}
        for relation in shortened['geometryDesign']['relations']:
            if '__diagonal__' not in relation['id']:
                continue
            endpoint = relation['b']
            column = members[endpoint['path'][0]]
            frame = self.provider.evaluate(column['template'], column['parameterValues']).frames[endpoint['interfaceId']]
            self.assertLessEqual(frame['center'][2], 1950 - 200 + 1e-6)
        self.assertEqual(members['cross-1']['parameterValues']['length'], 1200)
        self.assertEqual(members['diagonal-1']['parameterValues']['length'], 1500)
        self.assertEqual(grown, before)

    def test_two_real_brace_rules_share_columns_and_rebuild_independently(self):
        source = self.brace_source()
        original = copy.deepcopy(source)
        source['parameters'].extend([
            {'id': name, 'label': name, 'value': value, 'unit': 'mm', 'min': value, 'max': value}
            for name, value in (('secondBottom', 175), ('secondTop', 125))
        ])
        first = source['braceGenerationRules'][0]
        second = {**first, 'id': 'second-grid', 'columnInterfaceIndexStart': 2,
                  'horizontalMemberId': 'second-cross', 'diagonalMemberId': 'second-diagonal',
                  'bottomOffsetParameterId': 'secondBottom', 'topClearanceParameterId': 'secondTop'}
        source['braceGenerationRules'].append(second)
        prototypes = {m['id']: m for m in source['assembly']['members']}
        for identifier, reference in (('second-cross', 'cross-1'), ('second-diagonal', 'diagonal-1')):
            source['assembly']['members'].append({**copy.deepcopy(prototypes[reference]), 'id': identifier})
        for height, expected in ((1200, 18), (1500, 24), (1200, 18)):
            result = ResizeSolver(source, self.provider, {}, {'dimensions': [1216, 114, height]}).solve()
            rebuilt = result['document']
            members = {m['id']: m for m in rebuilt['assembly']['members']}
            self.assertEqual(len(members), expected)
            for rule in rebuilt['braceGenerationRules']:
                relations = [r for r in rebuilt['geometryDesign']['relations']
                             if r['id'].startswith('__brace__' + rule['id'] + '__')]
                self.assertEqual(len(relations), expected - 2)
                parity = rule['columnInterfaceIndexStart'] % 2
                horizontal_nodes = [int(r['b']['interfaceId'].split('.')[-1]) for r in relations
                                    if '__horizontal__' in r['id']]
                self.assertTrue(all(i % 2 == parity for i in horizontal_nodes))
                self.assertEqual(members[rule['horizontalMemberId']]['parameterValues']['length'], 1200)
                self.assertEqual(members[rule['diagonalMemberId']]['parameterValues']['length'], 1500)
            self.assertFalse([c for c in validate_component(ComponentDocument.model_validate(rebuilt)) if c['status'] == 'fail'])
            source = rebuilt
        # Every source specification continues to reference the same fixed
        # published CAD templates; the isolated fixture itself stays unchanged.
        self.assertEqual(original, self.brace_source())

    def test_auto_fill_selection_previews_without_modifying_source_and_can_reenable(self):
        source = self.brace_source()
        initial = copy.deepcopy(source)
        enabled = ResizeSolver(source, self.provider, {}, {'dimensions': [1216, 114, 1200], 'autoFillBraces': True}).solve()['document']
        count = len(enabled['assembly']['members'])
        self.assertEqual(count, 10)
        self.assertTrue(enabled['braceGenerationRules'][0]['enabled'])
        # Turning the checkbox off retains current rods and their saved user
        # geometry relationships. No extra instances are generated at 1500.
        disabled_solver = ResizeSolver(enabled, self.provider, {}, {'dimensions': [1216, 114, 1500], 'autoFillBraces': False})
        self.assertEqual(len(disabled_solver.document['assembly']['members']), count)
        self.assertFalse(disabled_solver.document['braceGenerationRules'][0]['enabled'])
        self.assertEqual(disabled_solver.brace_member_ids, set())
        disabled = disabled_solver.solve()['document']
        self.assertEqual(len(disabled['assembly']['members']), count)
        self.assertFalse(disabled['braceGenerationRules'][0]['enabled'])
        reenabling = ResizeSolver(disabled, self.provider, {}, {'dimensions': [1216, 114, 1500], 'autoFillBraces': True}).solve()['document']
        self.assertTrue(reenabling['braceGenerationRules'][0]['enabled'])
        self.assertEqual(len(reenabling['assembly']['members']), 13)
        self.assertEqual(source, initial)
        with self.assertRaises(ResizeFailure) as caught:
            ResizeSolver(copy.deepcopy(self.fixture['components']['upright']), self.provider, {}, {'autoFillBraces': True})
        self.assertEqual(caught.exception.code, 'BRACE_RULE_REQUIRED')

    def test_upright_frame_shrinks_width_and_height_without_losing_connections(self):
        source = copy.deepcopy(self.fixture['components']['upright'])
        before = copy.deepcopy(source)
        result = ResizeSolver(source, self.provider, {}, {'dimensions': [1000, 114, 2000]}).solve()
        self.assertTrue(np.allclose(result['dimensions'], [1000, 114, 2000], atol=.1))
        self.assertEqual(source, before)
        members = {m['id']: m for m in result['document']['assembly']['members']}
        for id in ('upright-left', 'upright-right'):
            self.assertLess(members[id]['parameterValues']['holePitch'], 75)
            self.assertAlmostEqual(members[id]['parameterValues']['length'], 2000, delta=.1)
            self.assertIn('upright.sideAttachmentConnections.part_face_left.029', {i['id'] for i in members[id]['interfaces']})
        self.assertGreaterEqual(len(result['relations']), 30)
        for relation in result['relations']:
            for side in ('a', 'b'):
                endpoint = relation[side]
                self.assertIn(endpoint['interfaceId'], self.provider.evaluate(members[endpoint['path'][0]]['template'], members[endpoint['path'][0]]['parameterValues']).frames)
        checked = ComponentDocument.model_validate(result['document'])
        self.assertFalse([c for c in validate_component(checked) if c['status'] == 'fail'])

    def test_source_guard_evaluates_an_occurrence_even_after_count_decreases(self):
        member = self.fixture['components']['upright']['assembly']['members'][0]
        id = 'upright.sideAttachmentConnections.part_face_left.029'
        occurrences = [i for i in member['resolvedInterfaces'] if i['id'] == id]
        self.assertEqual(len(occurrences), 1)
        parameters = {**member['parameterValues'], 'length': 2000, 'holePitch': 75, 'endMargin': 100}
        missing = self.provider.generation_margins(member['template'], parameters, occurrences)
        self.assertAlmostEqual(missing[id][1], -300)
        retained = self.provider.generation_margins(member['template'], {**parameters, 'holePitch': 60}, occurrences)
        self.assertAlmostEqual(retained[id][1], 120)

    def test_three_levels_rotated_repeated_components(self):
        from backend.tests.test_geometry_resize import document
        child = copy.deepcopy(self.fixture['components']['beam'])
        source = copy.deepcopy(child)
        ref = {'templateKind': 'component', 'publicationId': 'beam-fixture', 'templateId': 'beam-fixture', 'version': 1, 'sha256': 'b'*64}
        interfaces = [{**p['interface'], 'id': p['id'], 'name': p['name']} for p in child['ports']]
        members = [{'id': id, 'name': id, 'code': 'NESTED', 'role': '横梁', 'template': ref, 'interfaces': interfaces, 'parameterValues': {}, 'position': [x, 0, 0], 'rotation': [0, 0, np.pi/2]} for id, x in [('left', -300), ('right', 300)]]
        bay = document(members)
        bay['ports'][0]['source']['interfaceId'] = interfaces[0]['id']
        bay_ref = {**ref, 'publicationId': 'bay-fixture', 'templateId': 'bay-fixture', 'sha256': 'c'*64}
        root = document([{**copy.deepcopy(members[0]), 'id': 'bay', 'template': bay_ref, 'position': [0, 0, 0], 'rotation': [0, 0, 0], 'interfaces': [{**interfaces[0], 'id': 'out'}]}])
        root['ports'][0]['source']['interfaceId'] = 'out'
        publications = {('beam-fixture', 'b'*64): child, ('bay-fixture', 'c'*64): bay}
        info = ResizeSolver(root, self.provider, publications, {'inspectOnly': True}).solve()
        target = list(info['dimensions']); target[1] += 100
        relation = {'id': 'cross-level', 'a': {'path': ['bay', 'left'], 'interfaceId': interfaces[0]['id']}, 'b': {'path': ['bay', 'right'], 'interfaceId': interfaces[0]['id']}, 'mode': 'preserve-offset', 'origin': 'user', 'offset': [600, 0, 0]}
        result = ResizeSolver(root, self.provider, publications, {'dimensions': target, 'design': {'relations': [relation]}}).solve()
        self.assertTrue(np.allclose(result['dimensions'], target, atol=.1))
        self.assertEqual(child, source)
        children = result['document']['assembly']['members'][0]['instanceDocument']['assembly']['members']
        self.assertIsNot(children[0]['instanceDocument'], children[1]['instanceDocument'])
        self.assertTrue(all(m['instanceDocument']['geometryDesign']['verified'] for m in children))
        self.assertTrue(any(len(c['path']) == 3 for c in result['changes']))
        rebuilt = ResizeSolver(result['document'], self.provider, publications, {'inspectOnly': True}).solve()
        self.assertTrue(np.allclose(rebuilt['dimensions'], result['dimensions'], atol=.001))


if __name__ == '__main__': unittest.main()
