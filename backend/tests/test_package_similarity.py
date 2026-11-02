import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from backend.app.package_similarity import analyze_package, compare_packages


FIXTURE = Path(__file__).parent / "fixtures" / "package-similarity-v1.json"


def package(documents=None, *, order=None, output_hash="a" * 64, extra=None):
    documents = json.loads(FIXTURE.read_text(encoding="utf-8")) if documents is None else documents
    entries = {**documents, "outputs.json": {"artifacts": [{"sha256": output_hash, "packagePath": "geometry/preview.stl"}]}}
    if extra:
        entries.update(extra)
    names = order or list(entries)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, json.dumps(entries[name], ensure_ascii=False))
    return stream.getvalue()


class PackageSimilarityTests(unittest.TestCase):
    def test_zip_order_publication_identity_and_output_hash_do_not_change_semantic_score(self):
        first = package()
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))
        docs["manifest.json"].update(id="part-beta", code="OTHER", name="Renamed", author="author-b")
        second = package(docs, order=list(reversed(list(docs) + ["outputs.json"])), output_hash="b" * 64)

        result = compare_packages(first, second)

        self.assertNotEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())
        self.assertFalse(result["exactDuplicate"])
        self.assertEqual(result["scores"], {"G": 100.0, "P": 100.0, "F": 100.0, "M": 100.0, "S": 100.0})

    def test_missing_critical_geometry_document_is_unclassified(self):
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))
        docs.pop("geometry-recipe.json")

        result = analyze_package(package(docs))

        self.assertEqual(result["candidateClass"], "unclassified")
        self.assertEqual(result["reason"], "CRITICAL_GEOMETRY_MISSING")

    def test_random_geometry_entity_ids_and_references_do_not_change_score(self):
        first = package()
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))
        docs["geometry-recipe.json"]["operations"][0].update(id="random-operation", profileId="random-profile")
        docs["constraints.json"]["entities"][0]["id"] = "random-profile"
        docs["sketch-solver.json"]["entities"][0]["id"] = "random-profile"

        result = compare_packages(first, package(docs))

        self.assertEqual(result["scores"], {"G": 100.0, "P": 100.0, "F": 100.0, "M": 100.0, "S": 100.0})

    def test_parameter_behavior_change_only_changes_parameter_score(self):
        first = package()
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))
        docs["parameters.json"]["definitions"][0]["default"] = 210

        result = compare_packages(first, package(docs))

        self.assertLess(result["scores"]["P"], 100)
        self.assertEqual(result["scores"]["G"], 100)

    def test_exact_package_hash_is_classified_as_exact_duplicate(self):
        content = package()

        result = compare_packages(content, content)

        self.assertTrue(result["exactDuplicate"])
        self.assertEqual(result["candidateClass"], "exact-duplicate")

    def test_unsafe_zip_path_is_unclassified(self):
        result = analyze_package(package(extra={"../outside.json": {"x": 1}}))

        self.assertEqual(result["candidateClass"], "unclassified")
        self.assertEqual(result["reason"], "PACKAGE_INVALID")

    def test_missing_or_unsupported_manifest_is_unclassified(self):
        original = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for field, value in [('schemaVersion', '2.0'), ('unitSystem', 'inch'),
                             ('coordinateSystem', 'left-handed')]:
            with self.subTest(field=field):
                docs = json.loads(json.dumps(original))
                docs['manifest.json'][field] = value
                self.assertEqual(analyze_package(package(docs))['candidateClass'], 'unclassified')
        original.pop('manifest.json')
        self.assertEqual(analyze_package(package(original))['candidateClass'], 'unclassified')

    def test_geometry_order_and_parameter_behavior_remain_semantic(self):
        original = json.loads(FIXTURE.read_text(encoding='utf-8'))
        original['geometry-recipe.json']['operations'].append({'id': 'second', 'type': 'fillet', 'radius': 2})
        docs = json.loads(json.dumps(original))
        docs['geometry-recipe.json']['operations'].reverse()
        self.assertLess(compare_packages(package(original), package(docs))['scores']['G'], 100)
        for field, value in [('minimum', 99), ('unit', 'cm'), ('expression', '100 + 100')]:
            with self.subTest(field=field):
                docs = json.loads(json.dumps(original))
                docs['parameters.json']['definitions'][0][field] = value
                self.assertLess(compare_packages(package(original), package(docs))['scores']['P'], 100)

    def test_meaningful_strings_equal_to_entity_ids_are_not_remapped(self):
        original = json.loads(FIXTURE.read_text(encoding='utf-8'))
        original['material-requirements.json']['requirements'][0]['materialName'] = 'operation-a'
        docs = json.loads(json.dumps(original))
        docs['geometry-recipe.json']['operations'][0]['id'] = 'random-operation'
        docs['material-requirements.json']['requirements'][0]['materialName'] = 'random-operation'
        self.assertLess(compare_packages(package(original), package(docs))['scores']['M'], 100)

    def test_zip_count_size_and_duplicate_entries_are_rejected(self):
        content = package()
        for setting in ['MAX_ENTRIES', 'MAX_EXPANDED_BYTES', 'MAX_JSON_BYTES']:
            with self.subTest(setting=setting), patch('backend.app.package_similarity.' + setting, 1):
                self.assertEqual(analyze_package(content)['reason'], 'PACKAGE_TOO_LARGE')
        stream = io.BytesIO(content)
        with zipfile.ZipFile(stream, 'a') as archive:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr('parameters.json', '{}')
        self.assertEqual(analyze_package(stream.getvalue())['reason'], 'PACKAGE_INVALID')


if __name__ == "__main__":
    unittest.main()
