import hashlib
import io
import json
import unittest
import zipfile
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.engine.template_core import SemanticFaceLocator, TemplateDraft, lower_to_plan
from backend.engine.template_core.metamodel import SemanticFaceDefinition
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.library import LibraryError, PublishedTemplate
from backend.tests.test_preview import STL, metadata


class EngineSnapshotTests(unittest.TestCase):
    def test_semantic_face_locator_accepts_optional_coordinate_frame(self):
        locator = SemanticFaceLocator.model_validate({
            "kind": "profileEdge",
            "coordinateFrame": None,
            "operationId": "body.main",
            "profileSketchId": "sketch.section.main",
            "sourceEntityId": "edge.bottom",
        })

        self.assertIsNone(locator.coordinateFrame)
        self.assertNotIn("coordinateFrame", locator.model_dump())

        face = SemanticFaceDefinition(
            id="part.face.profile",
            label="型材截面",
            hostFrame="profile",
            locator=locator,
        )
        self.assertEqual(face.hostFrame, "profile")

    def test_user_parameter_changes_lowered_geometry(self):
        draft = TemplateDraft(name="preview")
        length = next(item for item in draft.parameterDefinitions if item.id == "length")

        short = lower_to_plan(draft, {"record": {"code": "STEEL"}}, overrides={"length": 120})
        long = lower_to_plan(draft, {"record": {"code": "STEEL"}}, overrides={"length": 180})

        self.assertNotEqual(short.inputHash, long.inputHash)
        self.assertNotEqual(short.operations, long.operations)
        self.assertEqual(length.default, 1000)

    def test_internal_parameters_cannot_be_overridden(self):
        draft = TemplateDraft(name="preview")

        with self.assertRaises(ValueError):
            lower_to_plan(draft, {"record": {"code": "STEEL"}}, overrides={"innerBendRadius": 99})

    def test_published_package_without_stl_rebuilds_and_updates_with_parameters(self):
        draft = TemplateDraft(name="可重建零件", id="draft-1", code="BEAM-1")
        draft.materialValidationSamples = [{
            "id": "material.nominal", "role": "nominal", "name": "steel", "bindingId": "mat-1",
            "bindingMode": "copy", "materialCode": "M1", "materialName": "steel", "materialThickness": 2,
            "requiredForAdmission": True,
        }]
        manifest = draft.model_dump(exclude={
            "parameterDefinitions", "variants", "sketch", "sweepPath", "blank", "admission",
            "materialRequirements", "materialValidationSamples", "geometryRecipe", "featureRules",
            "interfaces", "evidence", "aiProposals",
        })
        # Publication metadata is not a field of the CAD draft.
        manifest["previewModel"] = {"file": "preview.stl", "format": "stl"}
        documents = {
            "manifest.json": manifest,
            "outputs.json": {"success": False, "artifacts": []},
            "material-validation.json": {"samples": draft.materialValidationSamples, "resolved": [{
                "sampleId": "material.nominal", "material": {"id": "1", "code": "M1", "name": "steel", "type": "raw", "thickness": 2}, "provenance": {}, "mismatches": [],
            }]},
            "parameters.json": {"definitions": [item.model_dump() for item in draft.parameterDefinitions]},
            "geometry-recipe.json": draft.geometryRecipe.model_dump(),
            "feature-rules.json": {"reviewed": True, "rules": []},
            "constraints.json": draft.sketch.model_dump(),
            "sweep-path.json": {"status": "empty", "geometry": [], "constraints": []},
            "interfaces.json": {"coordinateSystem": draft.coordinateSystem, "interfaces": []},
            "variants.json": {"variants": [item.model_dump() for item in draft.variants]},
            "material-requirements.json": {"requirements": [item.model_dump() for item in draft.materialRequirements], "blank": draft.blank.model_dump()},
            "evidence.json": {"items": [], "aiProposals": []},
            "admission.json": {"policy": draft.admission.model_dump()},
        }
        documents.pop("outputs.json")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in documents.items():
                archive.writestr(name, json.dumps(value))
        package_data = stream.getvalue()
        digest = hashlib.sha256(package_data).hexdigest()
        metadata = {"publicationId": "version-1", "templateId": "draft-1", "version": 1, "code": "BEAM-1", "name": "可重建零件", "createdAt": "2026-10-17T00:00:00Z", "sha256": digest, "size": len(package_data), "downloadUrl": "/ignored"}

        def upstream(request):
            if request.url.path.endswith("/download"):
                return httpx.Response(200, content=package_data)
            if request.url.path.endswith("/version-1"):
                return httpx.Response(200, json=metadata)
            return httpx.Response(404)

        with TestClient(create_app(Settings(api_url="http://parts.local", api_key="secret"), transport=httpx.MockTransport(upstream), dist_dir=None)) as client:
            available = client.get(f"/api/published-templates/version-1/availability?sha256={digest}")
            self.assertEqual(available.status_code, 200, available.text)
            self.assertEqual(available.json(), {"available": True})
            info = client.get(f"/api/published-templates/version-1/reconstruction?sha256={digest}")
            self.assertEqual(info.status_code, 200, info.text)
            self.assertEqual(info.json()["parameters"][0]["id"], "length")
            self.assertEqual(info.json()["interfaces"], [])
            oversized = client.post(f"/api/published-templates/version-1/reconstruction/model?sha256={digest}", content=b"{" + b" " * (64 * 1024) + b"}", headers={"content-type": "application/json"})
            self.assertEqual(oversized.status_code, 413)
            small = client.post(f"/api/published-templates/version-1/reconstruction/model?sha256={digest}", json={"parameters": {"length": 120}})
            large = client.post(f"/api/published-templates/version-1/reconstruction/model?sha256={digest}", json={"parameters": {"length": 180}})
            self.assertEqual(small.status_code, 200, small.text[:500])
            self.assertEqual(large.status_code, 200, large.text[:500])
            self.assertEqual(small.headers["content-type"], "model/stl")
            self.assertNotEqual(small.headers["x-ruiware-sha256"], large.headers["x-ruiware-sha256"])
            rejected = client.post(f"/api/published-templates/version-1/reconstruction/model?sha256={digest}", json={"parameters": {"thickness": 99}})
            self.assertEqual(rejected.status_code, 422)
            for broken in ("parameter", "recipe", "material", "missing_file"):
                with self.subTest(broken=broken):
                    invalid = json.loads(json.dumps(documents))
                    if broken == "parameter":
                        invalid["parameters.json"]["definitions"] = [p for p in invalid["parameters.json"]["definitions"] if p["id"] != "length"]
                    elif broken == "recipe":
                        invalid["geometry-recipe.json"]["operations"] = []
                    elif broken == "material":
                        invalid["material-validation.json"]["resolved"] = []
                    else:
                        invalid.pop("parameters.json")
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                        for name, value in invalid.items():
                            archive.writestr(name, json.dumps(value))
                    package_data = stream.getvalue()
                    metadata.update(sha256=hashlib.sha256(package_data).hexdigest(), size=len(package_data))
                    available = client.get(f"/api/published-templates/version-1/availability?sha256={metadata['sha256']}")
                    self.assertEqual(available.status_code, 200, available.text)
                    self.assertEqual(available.json(), {"available": False})


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.publication = PublishedTemplate.model_validate(metadata(b"test-package"))
        draft = TemplateDraft(id="draft-1", code="BEAM-1", name="test")
        self.draft = draft.model_dump(mode="json")
        self.record = patch("backend.app.reconstruction.read_publication", new_callable=AsyncMock).start()
        self.record.return_value = self.publication
        self.read = patch("backend.app.reconstruction._read_template", new_callable=AsyncMock).start()
        self.read.return_value = self.publication, self.draft, {"record": {"thickness": 2}}
        self.worker = patch("backend.app.reconstruction._run_worker", return_value=STL).start()
        self.addCleanup(patch.stopall)
        self.client = TestClient(create_app(Settings(), dist_dir=None))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def check(self):
        return self.client.get(f"/api/published-templates/version-1/availability?sha256={self.publication.sha256}")

    def test_success_and_failure_are_cached_per_package_hash(self):
        self.assertEqual(self.check().json(), {"available": True})
        self.assertEqual(self.check().json(), {"available": True})
        self.assertEqual(self.worker.call_count, 1)
        self.worker.side_effect = LibraryError(422, "PARAMETER_INVALID")
        self.publication.sha256 = "b" * 64
        self.assertEqual(self.check().json(), {"available": False})
        self.assertEqual(self.check().json(), {"available": False})
        self.assertEqual(self.worker.call_count, 2)

    def test_missing_definitions_hidden_and_transient_connection_errors_retry(self):
        self.read.side_effect = LibraryError(422, "PACKAGE_UNSUPPORTED")
        self.assertEqual(self.check().json(), {"available": False})
        self.worker.assert_not_called()
        self.publication.sha256 = "c" * 64
        self.read.side_effect = LibraryError(502, "LIBRARY_UNREACHABLE")
        self.assertEqual(self.check().status_code, 502)
        self.read.side_effect = None
        self.assertEqual(self.check().json(), {"available": True})

    def test_default_preview_reuses_checked_model_but_overrides_still_rebuild(self):
        self.assertEqual(self.check().json(), {"available": True})
        url = f"/api/published-templates/version-1/reconstruction/model?sha256={self.publication.sha256}"
        result = self.client.post(url, json={"parameters": {}})
        self.assertEqual(result.content, STL)
        self.assertEqual(self.worker.call_count, 1)
        # Browsers serialize a whole-valued float default as an integer.
        result = self.client.post(url, json={"parameters": {"length": 1000}})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.worker.call_count, 1)
        self.client.post(url, json={"parameters": {"length": 120}})
        self.assertEqual(self.worker.call_count, 2)

    def test_unchanged_defaults_do_not_allow_protected_parameters_to_use_cache(self):
        self.assertEqual(self.check().json(), {"available": True})
        self.worker.side_effect = LibraryError(422, "PARAMETER_INVALID")
        url = f"/api/published-templates/version-1/reconstruction/model?sha256={self.publication.sha256}"
        self.assertEqual(self.client.post(url, json={"parameters": {"thickness": 2}}).status_code, 422)

    def test_worker_service_failure_is_not_cached_as_template_rejection(self):
        self.worker.side_effect = LibraryError(503, "MODEL_RECONSTRUCTION_FAILED")
        self.assertEqual(self.check().status_code, 503)
        self.worker.side_effect = None
        self.assertEqual(self.check().json(), {"available": True})

    @patch("backend.app.reconstruction.resolved_interfaces")
    def test_reconstruction_resolves_interfaces_with_current_parameter_values(self, resolve):
        resolve.return_value = ([{"id": "hole.1"}], [])
        query = json.dumps({"length": 800})
        response = self.client.get(
            f"/api/published-templates/version-1/reconstruction?sha256={self.publication.sha256}&parameters={query}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["resolvedInterfaces"], [{"id": "hole.1"}])
        self.assertTrue(response.json()["resolvedInterfacesAvailable"])
        resolve.assert_called_once_with(self.draft, {"record": {"thickness": 2}}, {"length": 800})

        resolve.reset_mock()
        invalid = self.client.get(
            f"/api/published-templates/version-1/reconstruction?sha256={self.publication.sha256}&parameters="
            + json.dumps({"innerBendRadius": 25})
        )
        self.assertEqual(invalid.status_code, 422)
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
