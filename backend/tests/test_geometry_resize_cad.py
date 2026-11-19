"""Published-shape regression fixtures: same engine path as production resize.

Fixtures contain six fixed template definitions (upright, cross brace, diagonal,
rectangular tube, end plate and angle), with no remote credentials or mutable DB.
"""
import copy
import json
from pathlib import Path
import unittest

import numpy as np

from backend.app.components.models import ComponentDocument
from backend.app.components.resize_geometry import CadProvider
from backend.app.components.resize_solver import ResizeSolver
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
        self.assertFalse([c for c in validate_component(checked) if c['status'] == 'fail'])
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
