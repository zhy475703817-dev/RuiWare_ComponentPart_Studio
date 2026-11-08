import json
from pathlib import Path
import unittest

from backend.app.components.interface_compatibility import assess_interface_compatibility
from backend.app.components.models import PartInterface


class InterfaceCompatibilityVectorTests(unittest.TestCase):
    def test_same_pin_hole_roles(self):
        for role in ('pin', 'hole'):
            item = PartInterface.model_validate(dict(id='axis', name='同轴接口', type='axis', tags=[], referenceFrame={'axis': 'z'}, mating={
                'version': 1, 'family': 'pin-hole', 'role': role, 'connectionMethod': 'clearance-fit',
                'dimensions': {'diameter': {'nominal': 10, 'lowerTolerance': -0.02, 'upperTolerance': 0.02, 'unit': 'mm'}},
                'allowedRelations': ['同轴'], 'capacity': 1}))
            self.assertTrue(assess_interface_compatibility(item, item, '同轴').compatible)
            other = item.model_copy(deep=True)
            other.mating.dimensions['diameter'].nominal = 12
            self.assertFalse(assess_interface_compatibility(item, other, '同轴').compatible)
            self.assertFalse(assess_interface_compatibility(other, item, '同轴').compatible)

    def test_template_surface_contracts(self):
        for method in ('welded', 'bolted'):
            data = dict(id='face', name='安装面', type='plane', tags=[], referenceFrame={'axis': 'z'}, mating={
                'version': 1, 'family': 'plane-contact', 'role': 'surface', 'connectionMethod': method,
                'dimensions': {}, 'allowedRelations': ['贴合'], 'capacity': 1})
            item = PartInterface.model_validate(data)
            self.assertTrue(assess_interface_compatibility(item, item, '贴合').compatible)
            self.assertFalse(assess_interface_compatibility(item, item, '重合').compatible)

    def test_shared_vectors_match_rule_version_status_and_reason_codes(self):
        fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "interface-compatibility-v1.json"
        vectors = json.loads(fixture.read_text(encoding="utf-8"))
        for vector in vectors["cases"]:
            with self.subTest(case=vector["name"]):
                assessment = assess_interface_compatibility(
                    PartInterface.model_validate(vector["a"]),
                    PartInterface.model_validate(vector["b"]),
                    vector.get("relation"),
                )
                self.assertEqual(assessment.ruleVersion, vectors["ruleVersion"])
                self.assertEqual(assessment.status, vector["status"])
                actual = sorted(item.code for item in assessment.checks if item.status != "pass")
                self.assertEqual(actual, sorted(vector["reasonCodes"]))


if __name__ == "__main__":
    unittest.main()
