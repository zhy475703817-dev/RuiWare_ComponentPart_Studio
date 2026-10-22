import hashlib
import io
import json
import unittest
import zipfile

import httpx
from fastapi.testclient import TestClient

from backend.engine.template_core import TemplateDraft, lower_to_plan
from backend.app.config import Settings
from backend.app.main import create_app


class EngineSnapshotTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
