"""AI creation-time findings must be early, conservative, and non-mutating."""
import copy
import unittest

from backend.app.mcp.authoring import authoring_report
from backend.app.components.resize_solver import fingerprint
from backend.tests.test_geometry_resize import document, member


def report(doc, history=None, checks=None):
    return authoring_report({"document": doc, "checks": checks or []}, history)


def codes(result):
    return {item["code"] for item in result["issues"]}


def design(**values):
    return {"dimensions": [10, 10, 100], "locks": [], "relations": [], "verified": False, **values}


class AuthoringTests(unittest.TestCase):
    def test_brace_mating_contract_requirement_is_reported_during_authoring(self):
        doc = document([member('a'), member('b')])
        doc['braceGenerationRules'] = [{'id': 'braces', 'horizontalMemberId': 'a', 'diagonalMemberId': 'b',
            'horizontalLeftInterfaceId': 'start', 'horizontalRightInterfaceId': 'end',
            'diagonalLeftInterfaceId': 'start', 'diagonalRightInterfaceId': 'end'}]
        original = copy.deepcopy(doc)
        self.assertIn('AUTHORING_BRACE_CONTRACT_UNCONFIRMED', codes(report(doc)))
        self.assertEqual(doc, original)
        doc['braceGenerationRules'][0]['connectionMode'] = 'geometry'
        self.assertNotIn('AUTHORING_BRACE_CONTRACT_UNCONFIRMED', codes(report(doc)))

    def test_reports_orphans_at_creation_without_mutating_or_auto_locking(self):
        doc = document([member("left"), member("beam")])
        before = copy.deepcopy(doc)
        result = report(doc)
        missing = [issue["path"] for issue in result["issues"] if issue["code"] == "AUTHORING_RELATION_UNCONFIRMED"]
        self.assertEqual(missing, [["left"], ["beam"]])
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["geometryVerified"])
        self.assertEqual(doc, before)

    def test_explicit_mates_and_slots_resolve_only_declared_members(self):
        for kind in ("mate", "slot", "relation"):
            with self.subTest(kind=kind):
                doc = document([member("a"), member("b"), member("orphan")])
                if kind == "mate":
                    doc["assembly"]["mates"] = [{"from": {"memberId": "a", "interfaceId": "end"}, "to": {"memberId": "b", "interfaceId": "start"}}]
                elif kind == "slot":
                    doc["slots"] = [{"baseInterface": {"memberId": "a", "interfaceId": "end"}, "memberId": "b", "interfaceId": "start"}]
                else:
                    doc["geometryDesign"] = design(relations=[{"a": {"path": ["a"], "interfaceId": "end"}, "b": {"path": ["b"], "interfaceId": "start"}}])
                missing = [issue["path"] for issue in report(doc)["issues"] if issue["code"] == "AUTHORING_RELATION_UNCONFIRMED"]
                self.assertEqual(missing, [["orphan"]])

    def test_parent_port_connection_does_not_hide_unlinked_nested_leaf(self):
        child = document([member("attached"), member("orphan")])
        nested = member("nested")
        nested.update(template={**nested["template"], "templateKind": "component"}, instanceDocument=child)
        doc = document([nested, member("outside")])
        doc["assembly"]["mates"] = [{"from": {"memberId": "nested", "interfaceId": "out"}, "to": {"memberId": "outside", "interfaceId": "end"}}]
        missing = [issue["path"] for issue in report(doc)["issues"] if issue["code"] == "AUTHORING_RELATION_UNCONFIRMED"]
        self.assertEqual(missing, [["nested", "orphan"]])
        del nested["instanceDocument"]
        self.assertIn("AUTHORING_NESTED_SOURCE_UNCHECKED", codes(report(doc)))

    def test_dimension_lock_is_not_whole_member_lock(self):
        doc = document([member("a"), member("b")])
        doc["geometryDesign"] = design(locks=[{"path": ["a"], "parameterId": "length"}, {"path": ["b"]}])
        missing = [issue["path"] for issue in report(doc)["issues"] if issue["code"] == "AUTHORING_RELATION_UNCONFIRMED"]
        self.assertEqual(missing, [["a"]])

    def test_parameter_limits_integer_and_discrete_specs_checked_before_resize(self):
        doc = document()
        part = doc["assembly"]["members"][0]
        part["parameterDefinitions"] = [{"id": "length", "label": "长度", "valueType": "integer", "minimum": 10, "maximum": 200, "default": 100, "allowedValues": [50, 100, 150]}]
        for value in (0, 201, 100.5, 110, True, "100"):
            with self.subTest(value=value):
                part["parameterValues"]["length"] = value
                self.assertIn("AUTHORING_PARAMETER_INVALID", codes(report(doc)))
                self.assertEqual(report(doc)["status"], "needs_attention")
        part["parameterValues"]["length"] = 150
        self.assertNotIn("AUTHORING_PARAMETER_INVALID", codes(report(doc)))

    def test_imported_verified_flag_or_stale_evidence_cannot_claim_pass(self):
        doc = document()
        doc["geometryDesign"] = design(verified=True)
        self.assertFalse(report(doc)["geometryVerified"])
        history = {"expectedRevision": 1, "appliedRevision": 2, "result": {"checksum": fingerprint(doc)}}
        doc["revision"] = 2
        self.assertTrue(report(doc, history)["geometryVerified"])
        self.assertEqual(report(doc, history)["issues"], [])
        doc["assembly"]["members"][0]["parameterValues"]["length"] = 125
        self.assertFalse(report(doc, history)["geometryVerified"])
        doc["revision"] = 3
        self.assertFalse(report(doc, history)["geometryVerified"])

    def test_basic_data_failures_not_hidden_by_geometry_evidence(self):
        doc = document()
        doc["geometryDesign"] = design(verified=True)
        history = {"expectedRevision": 1, "appliedRevision": 2, "result": {"checksum": fingerprint(doc)}}
        doc["revision"] = 2
        result = report(doc, history, [{"id": "parameters", "status": "fail", "message": "参数范围错误"}])
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("AUTHORING_DATA_INVALID", codes(result))
