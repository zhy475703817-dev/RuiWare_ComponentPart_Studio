import unittest

from backend.app.components.resize_solver import ResizeSolver
from backend.tests.test_geometry_resize import BoxProvider, document, member


class ResizeTemplateIdentityTests(unittest.TestCase):
    def test_mirrored_members_share_template_when_sha256_case_differs(self):
        left, right = member("left"), member("right")
        left["position"][0], right["position"][0] = -20, 20
        right["template"]["sha256"] = right["template"]["sha256"].upper()
        design = {"relations": [{
            "id": "pair",
            "a": {"path": ["left"], "interfaceId": "start"},
            "b": {"path": ["right"], "interfaceId": "start"},
            "mode": "preserve-offset",
            "origin": "user",
        }]}
        solver = ResizeSolver(document([left, right]), BoxProvider(), {}, {"design": design})
        constraints = solver._prepare_constraints(solver._measure_baseline())
        self.assertEqual(constraints.symmetries, [(("left",), ("right",), 0, 0, 1)])
        self.assertEqual(solver.document["assembly"]["members"][1]["template"]["sha256"], right["template"]["sha256"])
