import json
from pathlib import Path
import unittest

from backend.app.components.interface_compatibility import assess_interface_compatibility
from backend.app.components.models import PartInterface


class InterfaceCompatibilityVectorTests(unittest.TestCase):
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
