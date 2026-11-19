import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.app.components.models import ComponentDocument
from backend.app.components.repository import DraftRepository
from backend.app.components.service import create_component_document, ComponentError
from backend.app.components.resize_geometry import Geometry, exact_bounds, matrix
from backend.app.components.resize_solver import ResizeSolver, ResizeFailure


class BoxProvider:
    """Real OCCT solids with analytic, stable interface identities."""
    def evaluate(self, reference, parameters):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        length = parameters['length']
        if not 10 <= length <= 200:
            raise ValueError('长度超限')
        shape = BRepPrimAPI_MakeBox(10, 10, length).Shape()
        interfaces = [{'id': id, 'name': id, 'type': 'plane', 'tags': []} for id in ('start', 'end')]
        frames = {id: {'center': np.array([5., 5., z]), 'normal': np.array([0., 0., direction]), 'u': np.array([1., 0., 0.]), 'source': (id,)} for id, z, direction in [('start', 0, -1), ('end', length, 1)]}
        definitions = [{'id': 'length', 'label': '长度', 'unit': 'mm', 'valueType': 'number', 'minimum': 10, 'maximum': 200, 'default': 100, 'allowedValues': []}]
        return Geometry(shape, exact_bounds(shape), frames, interfaces, [], [], definitions)


def member(id, z=0):
    return {'id': id, 'name': id, 'code': 'TEST', 'role': id, 'template': {'publicationId': 'box', 'templateId': 'box', 'version': 1, 'sha256': 'a'*64},
            'position': [0, 0, z], 'rotation': [0, 0, 0], 'parameterValues': {'length': 100},
            'interfaces': [{'id': id, 'name': id, 'type': 'plane', 'tags': []} for id in ('start', 'end')]}


def document(members=None):
    d = create_component_document().model_dump(by_alias=True)
    d['definition']['code'] = 'RESIZE-TEST'
    d['assembly'] = {'members': members or [member('a')], 'mates': []}
    d['bindings'] = []; d['patterns'] = []
    d['ports'] = [{'id': 'out', 'name': '外接口', 'source': {'memberId': d['assembly']['members'][0]['id'], 'interfaceId': 'start'}, 'tags': []}]
    return d


class ResizeTests(unittest.TestCase):
    def test_single_member_changes_geometry_not_mesh_scale(self):
        original = document(); snapshot = copy.deepcopy(original)
        result = ResizeSolver(original, BoxProvider(), {}, {'dimensions': [10, 10, 150]}).solve()
        self.assertEqual(original, snapshot)
        self.assertAlmostEqual(result['document']['assembly']['members'][0]['parameterValues']['length'], 150, delta=.1)
        self.assertTrue(np.allclose(result['dimensions'], [10, 10, 150], atol=.1))
        self.assertTrue(result['document']['geometryDesign']['verified'])

    def test_chain_preserves_interfaces_and_bottom(self):
        d = document([member('a'), member('b', 100)])
        result = ResizeSolver(d, BoxProvider(), {}, {'dimensions': [10, 10, 300]}).solve()
        a, b = result['document']['assembly']['members']
        self.assertAlmostEqual(a['position'][2], 0, delta=.05)
        self.assertAlmostEqual(a['parameterValues']['length'] + a['position'][2], b['position'][2], delta=.05)
        self.assertGreater(a['parameterValues']['length'], 100)
        self.assertGreater(b['parameterValues']['length'], 100)

    def test_out_of_range_fails_without_mutation(self):
        d = document(); snapshot = copy.deepcopy(d)
        with self.assertRaises(ResizeFailure):
            ResizeSolver(d, BoxProvider(), {}, {'dimensions': [10, 10, 300]}).solve()
        self.assertEqual(d, snapshot)

    def test_existing_offset_uses_rotated_interface_normal(self):
        a = {**member('a'), 'rotation': [0, np.pi/2, 0]}
        b = {**member('b'), 'position': [105, 0, 0], 'rotation': [0, np.pi/2, 0]}
        d = document([a, b])
        d['assembly']['mates'] = [{'id': 'offset', 'from': {'memberId': 'a', 'interfaceId': 'end'}, 'to': {'memberId': 'b', 'interfaceId': 'start'}, 'relation': '重合', 'offset': 5}]
        result = ResizeSolver(d, BoxProvider(), {}, {'dimensions': [305, 10, 10]}).solve()
        a, b = result['document']['assembly']['members']
        self.assertAlmostEqual(b['position'][0]-(a['position'][0]+a['parameterValues']['length']), 5, delta=.05)

    def test_cancel_and_lock(self):
        with self.assertRaisesRegex(ResizeFailure, '取消'):
            ResizeSolver(document(), BoxProvider(), {}, {'dimensions': [10, 10, 150]}, cancelled=lambda: True).solve()
        with self.assertRaises(ResizeFailure):
            ResizeSolver(document(), BoxProvider(), {}, {'dimensions': [10, 10, 150], 'design': {'locks': [{'path': ['a']}], 'relations': []}}).solve()

    def test_nested_instances_are_isolated_and_rotated(self):
        child = document()
        template = {'templateKind': 'component', 'publicationId': 'child', 'templateId': 'child', 'version': 1, 'sha256': 'b'*64}
        left = {**member('left'), 'template': template, 'rotation': [0, np.pi/2, 0], 'interfaces': [{'id': 'out', 'name': 'out', 'type': 'plane', 'tags': []}], 'parameterValues': {}}
        right = {**copy.deepcopy(left), 'id': 'right', 'position': [0, 30, 0]}
        root = document([left, right]); root['ports'][0]['source']['interfaceId'] = 'out'
        relation = {'id': 'pair', 'a': {'path': ['left'], 'interfaceId': 'out'}, 'b': {'path': ['right'], 'interfaceId': 'out'}, 'mode': 'preserve-offset', 'origin': 'user', 'offset': [0, 30, 0]}
        source = copy.deepcopy(child)
        result = ResizeSolver(root, BoxProvider(), {('child', 'b'*64): child}, {'dimensions': [150, 40, 10], 'design': {'relations': [relation]}}).solve()
        self.assertEqual(child, source)
        instances = result['document']['assembly']['members']
        self.assertIsNot(instances[0]['instanceDocument'], instances[1]['instanceDocument'])
        self.assertAlmostEqual(instances[0]['instanceDocument']['assembly']['members'][0]['parameterValues']['length'], 150, delta=.1)
        self.assertTrue(instances[0]['instanceDocument']['geometryDesign']['verified'])
        self.assertTrue(instances[1]['instanceDocument']['geometryDesign']['interfaceSnapshots'])
        again = ResizeSolver(result['document'], BoxProvider(), {('child', 'b'*64): child}, {'dimensions': [175, 40, 10]}).solve()
        self.assertTrue(np.allclose(again['dimensions'], [175, 40, 10], atol=.1))

    def test_pattern_instances_are_preserved_and_not_repeated_twice(self):
        d = document()
        d['parameters'].extend([{'id': 'count', 'label': '数量', 'value': 2, 'unit': '个', 'min': 1, 'max': 3}, {'id': 'spacing', 'label': '间距', 'value': 100, 'unit': 'mm', 'min': 0, 'max': 500}])
        d['patterns'] = [{'id': 'repeat', 'label': '阵列', 'memberIds': ['a'], 'countParameterId': 'count', 'spacingParameterId': 'spacing', 'axis': 'z'}]
        result = ResizeSolver(d, BoxProvider(), {}, {'dimensions': [10, 10, 300]}).solve()
        self.assertEqual(len(result['document']['assembly']['members']), 2)
        again = ResizeSolver(result['document'], BoxProvider(), {}, {'dimensions': [10, 10, 350]}).solve()
        self.assertEqual(len(again['document']['assembly']['members']), 2)
        self.assertTrue(np.allclose(again['dimensions'], [10, 10, 350], atol=.1))

    def test_locked_sibling_instance_and_publication_do_not_change(self):
        child = document()
        reference = {'templateKind': 'component', 'publicationId': 'child', 'templateId': 'child', 'version': 1, 'sha256': 'b'*64}
        left = {**member('left'), 'template': reference, 'parameterValues': {}, 'interfaces': [{'id': 'out', 'name': 'out', 'type': 'plane', 'tags': []}]}
        right = {**copy.deepcopy(left), 'id': 'right', 'position': [30, 0, 0]}
        root = document([left, right]); root['ports'][0]['source']['interfaceId'] = 'out'
        relation = {'id': 'pair', 'a': {'path': ['left'], 'interfaceId': 'out'}, 'b': {'path': ['right'], 'interfaceId': 'out'}, 'mode': 'preserve-offset', 'origin': 'user', 'offset': [30, 0, 0]}
        result = ResizeSolver(root, BoxProvider(), {('child', 'b'*64): child}, {'dimensions': [40, 10, 150], 'design': {'relations': [relation], 'locks': [{'path': ['right']}]}}).solve()
        first, second = result['document']['assembly']['members']
        self.assertEqual(second['instanceDocument']['assembly']['members'][0]['parameterValues']['length'], 100)
        self.assertTrue(np.allclose(second['position'], right['position']))
        self.assertAlmostEqual(first['instanceDocument']['assembly']['members'][0]['parameterValues']['length'], 150, delta=.1)
        self.assertEqual(child['assembly']['members'][0]['parameterValues']['length'], 100)

    def test_no_guessed_connections_for_disconnected_members(self):
        d = document([member('a'), {**member('b'), 'position': [0, 30, 0]}])
        with self.assertRaises(ResizeFailure) as raised:
            ResizeSolver(d, BoxProvider(), {}, {'dimensions': [10, 40, 150]}).solve()
        self.assertEqual(raised.exception.code, 'RESIZE_NEEDS_CHOICE')

    def test_discrete_solutions_require_selection_and_exact_validation(self):
        class Discrete(BoxProvider):
            def evaluate(self, reference, parameters):
                g = super().evaluate(reference, parameters)
                g.definitions[0]['allowedValues'] = [100, 150, 200]
                return g
        d = document([member('a'), member('b', 100)])
        with self.assertRaises(ResizeFailure) as raised:
            ResizeSolver(d, Discrete(), {}, {'dimensions': [10, 10, 300]}).solve()
        self.assertEqual(raised.exception.code, 'RESIZE_NEEDS_CHOICE')
        self.assertEqual(len(raised.exception.details), 3)
        result = ResizeSolver(d, Discrete(), {}, {'dimensions': [10, 10, 300], 'solutionIndex': 1}).solve()
        self.assertTrue(np.allclose(result['dimensions'], [10, 10, 300], atol=.1))

    def test_removed_interface_is_rejected(self):
        class Changing(BoxProvider):
            def evaluate(self, reference, parameters):
                g = super().evaluate(reference, parameters)
                if parameters['length'] > 120: g.frames.pop('end')
                return g
        with self.assertRaises(ResizeFailure) as raised:
            ResizeSolver(document(), Changing(), {}, {'dimensions': [10, 10, 150]}).solve()
        self.assertEqual(raised.exception.code, 'RESIZE_INTERFACE_MISSING')

    def test_atomic_revision_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = DraftRepository(Path(directory)/'drafts.sqlite')
            saved = repo.create(document=ComponentDocument.model_validate(document()))
            result = ResizeSolver(saved['document'], BoxProvider(), {}, {'dimensions': [10, 10, 150]}).solve()
            applied = repo.replace_geometry(saved['draftId'], saved['document']['revision'], result['document'])
            self.assertEqual(applied['document']['revision'], saved['document']['revision']+1)
            with self.assertRaises(ComponentError): repo.replace_geometry(saved['draftId'], saved['document']['revision'], result['document'])
            self.assertTrue(repo.export(saved['draftId'])['geometryDesign']['verified'])
            restored = repo.replace_geometry(saved['draftId'], applied['document']['revision'], saved['document'])
            self.assertEqual(restored['document']['assembly'], saved['document']['assembly'])


if __name__ == '__main__': unittest.main()
