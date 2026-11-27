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
from backend.app.components.brace_generation import expand_brace_generation_rules


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


class HoleRowProvider(BoxProvider):
    """A size-dependent source array whose last occurrence can disappear."""
    def evaluate(self, reference, parameters):
        g = super().evaluate(reference, parameters)
        pitch = parameters.get('pitch', 19.)
        g.definitions.append({'id': 'pitch', 'label': '孔距', 'unit': 'mm', 'valueType': 'number',
                              'minimum': 10, 'maximum': 30, 'default': 19, 'allowedValues': []})
        for index in range(max(0, int(np.floor((parameters['length']-20)/pitch))+1)):
            id = f'holes.{index+1:03d}'
            g.frames[id] = {'center': np.array([5., 5., 10+index*pitch]), 'normal': np.array([1., 0., 0.]),
                            'u': np.array([0., 1., 0.]), 'source': ('holes', 'row', id, ('face',)), 'featureAxis': True}
            g.interfaces.append({'id': id, 'name': id, 'type': 'axis', 'tags': []})
            g.resolved.append({'id': id, 'declarationMode': 'featureDerived', 'sourceFeatureRuleId': 'row', 'sourceFeatureId': id})
        return g

    def generation_margins(self, reference, parameters, occurrences):
        return {i['id']: np.array([(int(i['id'].split('.')[-1])-1)*parameters.get('pitch', 19.),
                                   parameters['length']-20-(int(i['id'].split('.')[-1])-1)*parameters.get('pitch', 19.)])
                for i in occurrences}


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
    def test_brace_rule_materializes_dynamic_provider_holes_before_expansion(self):
        d = document()
        d["parameters"] = [
            {"id": "height", "label": "高度", "value": 120, "unit": "mm", "min": 100, "max": 1000},
            {"id": "pitch", "label": "孔距", "value": 20, "unit": "mm", "min": 10, "max": 300},
            {"id": "bottom", "label": "底部余量", "value": 20, "unit": "mm", "min": 0, "max": 100},
            {"id": "top", "label": "顶部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
        ]
        def brace_member(identifier):
            item = member(identifier)
            item["interfaces"] = [{"id": name, "name": name, "type": "plane", "tags": []} for name in ("left", "right")]
            item["parameterValues"] = {"length": 80}
            return item
        left, right = member("left"), member("right")
        left["parameterValues"] = right["parameterValues"] = {"length": 120}
        horizontal, diagonal = brace_member("horizontal"), brace_member("diagonal")
        d["assembly"] = {"members": [left, right, horizontal, diagonal], "mates": []}
        d["braceGenerationRules"] = [{
            "id": "provider-grid", "label": "动态孔列撑杆", "heightParameterId": "height", "pitchParameterId": "pitch",
            "bottomOffsetParameterId": "bottom", "topClearanceParameterId": "top", "leftColumnMemberId": "left",
            "rightColumnMemberId": "right", "horizontalMemberId": "horizontal", "diagonalMemberId": "diagonal",
            "horizontalLeftInterfaceId": "left", "horizontalRightInterfaceId": "right",
            "diagonalLeftInterfaceId": "left", "diagonalRightInterfaceId": "right",
            "leftColumnInterfacePattern": "holes.{index:03d}", "rightColumnInterfacePattern": "holes.{index:03d}",
            "columnInterfaceIndexStart": 1, "columnInterfaceIndexStep": 1, "remainderPolicy": "reject",
        }]
        solver = ResizeSolver(d, HoleRowProvider(), {}, {"dimensions": [10, 10, 120]})
        source = {m["id"]: m for m in solver.document["assembly"]["members"]}
        self.assertEqual(
            {item["id"] for item in source["left"]["interfaces"] if item["id"].startswith("holes.")},
            {f"holes.{index:03d}" for index in range(1, 7)},
        )
        self.assertEqual(
            sum(member.get("generatedBy") == "provider-grid" for member in solver.document["assembly"]["members"]),
            9,
        )

    def test_brace_rule_materializes_target_height_through_member_binding(self):
        d = document()
        d["parameters"] = [
            {"id": "height", "label": "高度", "value": 120, "unit": "mm", "min": 100, "max": 1000},
            {"id": "pitch", "label": "孔距", "value": 20, "unit": "mm", "min": 10, "max": 300},
            {"id": "bottom", "label": "底部余量", "value": 20, "unit": "mm", "min": 0, "max": 100},
            {"id": "top", "label": "顶部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
        ]
        def brace_member(identifier):
            item = member(identifier)
            item["interfaces"] = [{"id": name, "name": name, "type": "plane", "tags": []} for name in ("left", "right")]
            item["parameterValues"] = {"length": 80}
            return item
        left, right = member("left"), member("right")
        left["parameterValues"] = right["parameterValues"] = {"length": 120}
        horizontal, diagonal = brace_member("horizontal"), brace_member("diagonal")
        d["assembly"] = {"members": [left, right, horizontal, diagonal], "mates": []}
        d["bindings"] = [
            {"id": "height-left", "parameterId": "height", "memberId": "left", "memberParameterId": "length"},
            {"id": "height-right", "parameterId": "height", "memberId": "right", "memberParameterId": "length"},
        ]
        d["braceGenerationRules"] = [{
            "id": "target-grid", "label": "目标高度动态孔列撑杆", "heightParameterId": "height", "pitchParameterId": "pitch",
            "bottomOffsetParameterId": "bottom", "topClearanceParameterId": "top", "leftColumnMemberId": "left",
            "rightColumnMemberId": "right", "horizontalMemberId": "horizontal", "diagonalMemberId": "diagonal",
            "horizontalLeftInterfaceId": "left", "horizontalRightInterfaceId": "right",
            "diagonalLeftInterfaceId": "left", "diagonalRightInterfaceId": "right",
            "leftColumnInterfacePattern": "holes.{index:03d}", "rightColumnInterfacePattern": "holes.{index:03d}",
            "columnInterfaceIndexStart": 1, "columnInterfaceIndexStep": 1, "remainderPolicy": "reject",
        }]
        solver = ResizeSolver(d, HoleRowProvider(), {}, {"dimensions": [10, 10, 180]})
        source = {m["id"]: m for m in solver.document["assembly"]["members"]}
        self.assertEqual(source["left"]["parameterValues"]["length"], 180)
        self.assertEqual(
            {item["id"] for item in source["left"]["interfaces"] if item["id"].startswith("holes.")},
            {f"holes.{index:03d}" for index in range(1, 10)},
        )
        parameters = {leaf["path"]: dict(leaf["member"]["parameterValues"]) for leaf in solver.leaves}
        transforms = {leaf["path"]: leaf["world"] for leaf in solver.leaves}
        path = ("left",)
        measured, frames, _, _ = solver.measure(parameters, transforms)
        self.assertEqual(measured[path].bounds[1, 2], 180)
        self.assertTrue(all((path, f"holes.{index:03d}") in frames for index in range(1, 10)))
        self.assertEqual(
            sum(member.get("generatedBy") == "target-grid" for member in solver.document["assembly"]["members"]),
            15,
        )
        generated_braces = [member for member in solver.document["assembly"]["members"]
                            if member.get("generatedBy") == "target-grid"]
        self.assertTrue(generated_braces)
        self.assertTrue(all(member["parameterValues"]["length"] == 80 for member in generated_braces))
        inspected = ResizeSolver(d, HoleRowProvider(), {}, {
            'inspectOnly': True, 'dimensions': [10, 10, 180], 'design': {'locks': [{'path': ['left']}]},
        })
        self.assertEqual(inspected.by_path[('left',)]['member']['parameterValues']['length'], 120)
        self.assertEqual(next(p['value'] for p in inspected.document['parameters'] if p['id'] == 'height'), 120)

        original = copy.deepcopy(d)
        for lock in ({'path': ['left']}, {'path': ['left'], 'parameterId': 'length'}):
            with self.subTest(lock=lock), self.assertRaises(ResizeFailure) as caught:
                ResizeSolver(d, HoleRowProvider(), {}, {
                    'dimensions': [10, 10, 180], 'design': {'locks': [lock]},
                })
            self.assertEqual(caught.exception.code, 'RESIZE_LOCK_CONFLICT')
            self.assertEqual(caught.exception.details[0]['before'], 120)
            self.assertEqual(caught.exception.details[0]['after'], 180)
        self.assertEqual(d, original)

        binding_driven = copy.deepcopy(d)
        binding_driven['assembly']['members'][0]['parameterValues']['length'] = 100
        current_height = ResizeSolver(binding_driven, HoleRowProvider(), {}, {
            'dimensions': [10, 10, 120], 'design': {'locks': [{'path': ['left']}]},
        })
        self.assertEqual(current_height.by_path[('left',)]['member']['parameterValues']['length'], 120)
        with self.assertRaises(ResizeFailure) as caught:
            ResizeSolver(binding_driven, HoleRowProvider(), {}, {
                'dimensions': [10, 10, 180], 'design': {'locks': [{'path': ['left']}]},
            })
        self.assertEqual(caught.exception.details[0]['before'], 120)

        # Saved locks apply even when no design override is sent. Unrelated
        # dimensions remain legal, rather than blocking every column lock.
        saved = copy.deepcopy(d)
        saved['geometryDesign'] = {'locks': [{'path': ['left'], 'parameterId': 'length'}]}
        with self.assertRaises(ResizeFailure) as caught:
            ResizeSolver(saved, HoleRowProvider(), {}, {'dimensions': [10, 10, 180]})
        self.assertEqual(caught.exception.code, 'RESIZE_LOCK_CONFLICT')
        allowed = ResizeSolver(d, HoleRowProvider(), {}, {
            'dimensions': [10, 10, 180], 'design': {'locks': [{'path': ['left'], 'parameterId': 'pitch'}]},
        })
        self.assertEqual(allowed.by_path[('left',)]['member']['parameterValues']['length'], 180)

        # A shortened rule must not silently remove a locked generated rod.
        expanded = copy.deepcopy(solver.document)
        top = max((m for m in expanded['assembly']['members'] if m.get('generatedBy') == 'target-grid'),
                  key=lambda m: m['generatedIndex'])
        for lock in ({'path': [top['id']]}, {'path': [top['id']], 'parameterId': 'length'}):
            with self.subTest(removed_lock=lock), self.assertRaises(ResizeFailure) as caught:
                ResizeSolver(expanded, HoleRowProvider(), {}, {
                    'dimensions': [10, 10, 120], 'design': {'locks': [lock]},
                })
            self.assertEqual(caught.exception.code, 'RESIZE_LOCK_CONFLICT')
            self.assertEqual(caught.exception.details[0]['path'], [top['id']])

        # A dimension lock allows rigid repositioning. A whole-member lock
        # preserves the input pose even if CAD seeding proposes another one.
        from unittest.mock import patch
        def move_seed(brace, *_args):
            brace['position'] = [20., 30., 40.]
            brace['rotation'] = [.1, .2, .3]
        with patch('backend.app.components.brace_generation._place_brace', side_effect=move_seed):
            locked = ResizeSolver(d, HoleRowProvider(), {}, {
                'dimensions': [10, 10, 180], 'design': {'locks': [{'path': ['horizontal']}]},
            })
            size_locked = ResizeSolver(d, HoleRowProvider(), {}, {
                'dimensions': [10, 10, 180], 'design': {'locks': [{'path': ['horizontal'], 'parameterId': 'length'}]},
            })
        self.assertEqual(locked.by_path[('horizontal',)]['member']['position'], horizontal['position'])
        self.assertEqual(locked.by_path[('horizontal',)]['member']['rotation'], horizontal['rotation'])
        self.assertEqual(size_locked.by_path[('horizontal',)]['member']['position'], [20., 30., 40.])

    def test_brace_rule_rebuilds_fixed_instances_from_hole_grid_without_accumulating(self):
        d = document()
        d["parameters"] = [
            {"id": "height", "label": "高度", "value": 200, "unit": "mm", "min": 100, "max": 1000},
            {"id": "pitch", "label": "孔距", "value": 100, "unit": "mm", "min": 50, "max": 300},
            {"id": "bottom", "label": "底部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
            {"id": "top", "label": "顶部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
        ]
        def brace_member(identifier):
            item = member(identifier)
            item["interfaces"] = [{"id": name, "name": name, "type": "plane", "tags": []} for name in ("left", "right")]
            item["parameterValues"] = {"length": 80}
            return item
        left, right = member("left"), member("right")
        for column in (left, right):
            column["interfaces"] = [{"id": f"hole.{index:03d}", "name": "孔位", "type": "axis", "tags": []} for index in range(1, 5)]
        horizontal, diagonal = brace_member("horizontal"), brace_member("diagonal")
        d["assembly"] = {"members": [left, right, horizontal, diagonal], "mates": []}
        d["braceGenerationRules"] = [{
            "id": "brace-grid", "label": "柱片撑杆", "heightParameterId": "height", "pitchParameterId": "pitch",
            "bottomOffsetParameterId": "bottom", "topClearanceParameterId": "top", "leftColumnMemberId": "left",
            "rightColumnMemberId": "right", "horizontalMemberId": "horizontal", "diagonalMemberId": "diagonal",
            "horizontalLeftInterfaceId": "left", "horizontalRightInterfaceId": "right",
            "diagonalLeftInterfaceId": "left", "diagonalRightInterfaceId": "right",
            "leftColumnInterfacePattern": "hole.{index:03d}", "rightColumnInterfacePattern": "hole.{index:03d}",
            "columnInterfaceIndexStart": 1, "columnInterfaceIndexStep": 1, "remainderPolicy": "reject",
        }]
        expand_brace_generation_rules(d)
        self.assertEqual(sum(m.get("generatedBy") == "brace-grid" for m in d["assembly"]["members"]), 3)
        self.assertEqual(len(d["assembly"]["mates"]), 10)
        horizontal_targets = {
            mate["from"]["memberId"]: mate["to"]["interfaceId"]
            for mate in d["assembly"]["mates"]
            if mate["id"].startswith("__brace__brace-grid__horizontal") and mate["from"]["memberId"]
        }
        self.assertEqual(
            {target for member_id, target in horizontal_targets.items() if member_id == "horizontal"},
            {"hole.001"},
        )
        self.assertEqual(
            {target for member_id, target in horizontal_targets.items() if member_id.endswith("-1")},
            {"hole.002"},
        )
        self.assertEqual(
            {target for member_id, target in horizontal_targets.items() if member_id.endswith("-2")},
            {"hole.003"},
        )
        snapshot = copy.deepcopy(d["assembly"])
        expand_brace_generation_rules(d)
        self.assertEqual(d["assembly"], snapshot)
        self.assertEqual({m["id"] for m in d["assembly"]["members"] if m.get("generatedBy")},
                         {"generated-brace-brace-grid-horizontal-1", "generated-brace-brace-grid-horizontal-2",
                          "generated-brace-brace-grid-diagonal-1"})

        geometry = copy.deepcopy(d)
        geometry["braceGenerationRules"][0]["connectionMode"] = "geometry"
        original_mate = {"id": "user-connection", "from": {"memberId": "horizontal", "interfaceId": "left"},
                         "to": {"memberId": "left", "interfaceId": "hole.001"}, "relation": "贴合", "offset": 0}
        geometry["assembly"]["mates"].append(original_mate)
        geometric_relations = expand_brace_generation_rules(geometry)
        self.assertEqual(geometry["assembly"]["mates"], [original_mate])
        self.assertEqual(len(geometric_relations), 10)
        self.assertTrue(all(relation["mode"] == "coincident" for relation in geometric_relations))
        self.assertTrue(all(relation["a"]["path"] and relation["b"]["interfaceId"].startswith("hole.")
                            for relation in geometric_relations))
        self.assertEqual(expand_brace_generation_rules(geometry), geometric_relations)
        self.assertNotIn("geometryDesign", geometry)

        declared = copy.deepcopy(d)
        declared["assembly"]["mates"].append(original_mate)
        expand_brace_generation_rules(declared)
        self.assertIn(original_mate, declared["assembly"]["mates"])
        self.assertEqual(len(declared["assembly"]["mates"]), 10)
        external = {"id": "user-attached-to-top-brace", "from": {"memberId": "generated-brace-brace-grid-horizontal-2", "interfaceId": "left"},
                    "to": {"memberId": "right", "interfaceId": "hole.004"}, "relation": "贴合", "offset": 0}
        declared["assembly"]["mates"].append(external)
        declared["parameters"][0]["value"] = 100
        expand_brace_generation_rules(declared)
        self.assertIn(external, declared["assembly"]["mates"])
        self.assertNotIn("generated-brace-brace-grid-horizontal-2", {m["id"] for m in declared["assembly"]["members"]})

        foreign = brace_member("foreign-generated")
        foreign["generatedBy"] = "other-rule"
        d["assembly"]["members"].append(foreign)
        d["assembly"]["mates"].append({
            "id": "__brace__other-rule__keep",
            "from": {"memberId": foreign["id"], "interfaceId": "left"},
            "to": {"memberId": "left", "interfaceId": "hole.001"},
            "relation": "贴合", "offset": 0,
        })
        foreign_snapshot = copy.deepcopy(foreign)
        foreign_mate_snapshot = copy.deepcopy(d["assembly"]["mates"][-1])
        expand_brace_generation_rules(d)
        self.assertEqual(next(item for item in d["assembly"]["members"] if item["id"] == foreign["id"]), foreign_snapshot)
        self.assertIn(foreign_mate_snapshot, d["assembly"]["mates"])
        self.assertEqual(
            {item["id"] for item in d["assembly"]["members"] if item.get("generatedBy") == "brace-grid"},
            {"generated-brace-brace-grid-horizontal-1", "generated-brace-brace-grid-horizontal-2", "generated-brace-brace-grid-diagonal-1"},
        )

        oversized = copy.deepcopy(d)
        oversized["parameters"][0]["value"] = 1_000_000
        oversized["parameters"][1]["value"] = 1
        oversized["braceGenerationRules"][0]["remainderPolicy"] = "top-clearance"
        oversized['parameters'][0]['max'] = 1_000_000
        oversized['parameters'][1]['min'] = 1
        with self.assertRaises(ResizeFailure) as error:
            expand_brace_generation_rules(oversized)
        self.assertEqual(error.exception.code, "RESIZE_RESOURCE_LIMIT")

        collision = copy.deepcopy(d)
        next(item for item in collision["assembly"]["members"]
             if item["id"] == "generated-brace-brace-grid-horizontal-1").pop("generatedBy")
        with self.assertRaises(ResizeFailure) as error:
            expand_brace_generation_rules(collision)
        self.assertEqual(error.exception.code, "BRACE_RULE_MEMBER_COLLISION")

    def test_brace_rule_rejects_non_modular_height(self):
        d = document()
        d["parameters"] = [
            {"id": "height", "label": "高度", "value": 250, "unit": "mm", "min": 100, "max": 1000},
            {"id": "pitch", "label": "孔距", "value": 100, "unit": "mm", "min": 50, "max": 300},
            {"id": "bottom", "label": "底部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
            {"id": "top", "label": "顶部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
        ]
        d["braceGenerationRules"] = [{"id": "invalid", "heightParameterId": "height", "pitchParameterId": "pitch",
            "bottomOffsetParameterId": "bottom", "topClearanceParameterId": "top"}]
        with self.assertRaises(ResizeFailure) as error:
            expand_brace_generation_rules(d)
        self.assertEqual(error.exception.code, "BRACE_RULE_INVALID")

    def test_brace_rule_diagonal_spans_multiple_hole_pitches(self):
        d = document()
        d["parameters"] = [
            {"id": "height", "label": "高度", "value": 400, "unit": "mm", "min": 100, "max": 1000},
            {"id": "pitch", "label": "孔距", "value": 100, "unit": "mm", "min": 50, "max": 300},
            {"id": "bottom", "label": "底部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
            {"id": "top", "label": "顶部余量", "value": 0, "unit": "mm", "min": 0, "max": 100},
        ]
        columns = [member("left"), member("right")]
        for column in columns:
            column["interfaces"] = [
                {"id": f"hole.{index:03d}", "name": "孔位", "type": "axis", "tags": []}
                for index in range(1, 6)
            ]
        def brace_member(identifier):
            item = member(identifier)
            item["interfaces"] = [{"id": name, "name": name, "type": "plane", "tags": []} for name in ("left", "right")]
            item["parameterValues"] = {"length": 80}
            return item
        horizontal, diagonal = brace_member("horizontal"), brace_member("diagonal")
        d["assembly"] = {"members": [*columns, horizontal, diagonal], "mates": []}
        d["braceGenerationRules"] = [{
            "id": "stepped-grid", "heightParameterId": "height", "pitchParameterId": "pitch",
            "bottomOffsetParameterId": "bottom", "topClearanceParameterId": "top",
            "leftColumnMemberId": "left", "rightColumnMemberId": "right",
            "horizontalMemberId": "horizontal", "diagonalMemberId": "diagonal",
            "horizontalLeftInterfaceId": "left", "horizontalRightInterfaceId": "right",
            "diagonalLeftInterfaceId": "left", "diagonalRightInterfaceId": "right",
            "leftColumnInterfacePattern": "hole.{index:03d}", "rightColumnInterfacePattern": "hole.{index:03d}",
            "columnInterfaceIndexStart": 1, "columnInterfaceIndexStep": 2,
            "diagonalColumnInterfaceIndexStep": 2, "remainderPolicy": "reject",
        }]
        expand_brace_generation_rules(d)
        diagonal_mates = [mate for mate in d["assembly"]["mates"] if "__diagonal__" in mate["id"]]
        self.assertEqual(len(diagonal_mates), 4)
        horizontal_mates = [mate for mate in d["assembly"]["mates"] if "__horizontal__" in mate["id"] and mate["id"].endswith("__left")]
        self.assertEqual({mate["to"]["interfaceId"] for mate in horizontal_mates}, {"hole.001", "hole.003", "hole.005"})
        generated_horizontal = next(item for item in d["assembly"]["members"]
                                    if item.get("generatedBy") == "stepped-grid" and "horizontal" in item["id"])
        self.assertEqual(generated_horizontal["position"][2], 200)
        self.assertEqual(
            {(mate["from"]["memberId"], mate["to"]["interfaceId"]) for mate in diagonal_mates if mate["id"].endswith("__left")},
            {("diagonal", "hole.001"), ("generated-brace-stepped-grid-diagonal-1", "hole.003")},
        )
        generated_diagonal = next(item for item in d["assembly"]["members"] if item.get("generatedBy") == "stepped-grid" and "diagonal" in item["id"])
        self.assertEqual(generated_diagonal["position"][2], 200)
        self.assertEqual(generated_diagonal["parameterValues"]["length"], 80)

        too_short = copy.deepcopy(d)
        too_short["parameters"][0]["value"] = 200
        too_short["braceGenerationRules"][0]["diagonalColumnInterfaceIndexStep"] = 3
        with self.assertRaises(ResizeFailure) as error:
            expand_brace_generation_rules(too_short)
        self.assertEqual(error.exception.code, "BRACE_RULE_TOO_SHORT")
        self.assertEqual(error.exception.details, [{"minimumHeight": 300}])

        for field, value in (("columnInterfaceIndexStep", 1.5), ("columnInterfaceIndexStart", True),
                             ("diagonalColumnInterfaceIndexStart", 10001)):
            invalid = copy.deepcopy(d)
            invalid["braceGenerationRules"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ResizeFailure) as error:
                expand_brace_generation_rules(invalid)
            self.assertEqual(error.exception.code, "BRACE_RULE_RANGE_INVALID")

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

    def test_shrink_can_remove_only_unused_generated_occurrences(self):
        source = document(); before = copy.deepcopy(source)
        result = ResizeSolver(source, HoleRowProvider(), {}, {'dimensions': [10, 10, 80]}).solve()
        ids = {i['id'] for i in result['document']['assembly']['members'][0]['interfaces']}
        self.assertNotIn('holes.005', ids)
        self.assertIn('end', ids)
        self.assertEqual(source, before)

    def test_shrink_adjusts_pitch_to_keep_exported_occurrence(self):
        source = document()
        source['ports'][0]['source']['interfaceId'] = 'holes.005'
        result = ResizeSolver(source, HoleRowProvider(), {}, {'dimensions': [10, 10, 80]}).solve()
        member = result['document']['assembly']['members'][0]
        self.assertLess(member['parameterValues']['pitch'], 15)
        self.assertEqual(result['document']['ports'][0]['interface']['id'], 'holes.005')
        self.assertTrue(np.allclose(result['dimensions'], [10, 10, 80], atol=.1))

    def test_locked_pitch_cannot_silently_drop_exported_occurrence(self):
        source = document(); source['ports'][0]['source']['interfaceId'] = 'holes.005'
        before = copy.deepcopy(source)
        with self.assertRaises(ResizeFailure):
            ResizeSolver(source, HoleRowProvider(), {}, {'dimensions': [10, 10, 80],
                'design': {'locks': [{'path': ['a'], 'parameterId': 'pitch'}]}}).solve()
        self.assertEqual(source, before)

    def test_unoccupied_slot_still_protects_its_base_occurrence(self):
        source = document()
        source['slots'] = [{'id': 'future', 'name': '预留槽位', 'memberId': None, 'interfaceId': None,
                            'required': False, 'baseInterface': {'memberId': 'a', 'interfaceId': 'holes.005'}}]
        result = ResizeSolver(source, HoleRowProvider(), {}, {'dimensions': [10, 10, 80]}).solve()
        member = result['document']['assembly']['members'][0]
        self.assertLess(member['parameterValues']['pitch'], 15)
        self.assertIn('holes.005', {i['id'] for i in member['interfaces']})

    def test_missing_connected_occurrence_reports_member_and_interface(self):
        class Missing(HoleRowProvider):
            def evaluate(self, reference, parameters):
                g = super().evaluate(reference, parameters)
                if parameters['length'] < 90: g.frames.pop('holes.001', None)
                return g
        source = document()
        relation = {'id': 'connection', 'a': {'path': ['a'], 'interfaceId': 'holes.001'},
                    'b': {'path': ['b'], 'interfaceId': 'holes.001'}, 'mode': 'preserve-offset',
                    'origin': 'user', 'offset': [30, 0, 0]}
        source['assembly']['members'].append({**member('b'), 'position': [30, 0, 0]})
        before = copy.deepcopy(source)
        with self.assertRaises(ResizeFailure) as raised:
            ResizeSolver(source, Missing(), {}, {'dimensions': [40, 10, 80], 'design': {'relations': [relation]}}).solve()
        self.assertEqual(raised.exception.code, 'RESIZE_NO_SOLUTION')
        self.assertEqual(raised.exception.details[0]['interfaceId'], 'holes.001')
        self.assertIn('a', raised.exception.details[0]['path'])
        self.assertEqual(source, before)

    def test_unused_generated_occurrence_cannot_change_source(self):
        class Changing(HoleRowProvider):
            def evaluate(self, reference, parameters):
                g = super().evaluate(reference, parameters)
                if parameters['length'] < 90: g.frames['holes.001']['source'] = ('another-source',)
                return g
        with self.assertRaises(ResizeFailure) as raised:
            ResizeSolver(document(), Changing(), {}, {'dimensions': [10, 10, 80]}).solve()
        self.assertEqual(raised.exception.code, 'RESIZE_INTERFACE_MISSING')
        self.assertEqual(raised.exception.details[0]['interfaceId'], 'holes.001')

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
