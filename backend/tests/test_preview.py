import hashlib
import io
import json
import struct
import asyncio
import unittest
import zipfile
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


STL = b"component preview".ljust(80, b"\0") + struct.pack("<I12fH", 1, 0, 0, 1, 0, 0, 0, 100, 0, 0, 0, 50, 25, 0)
MODEL_HASH = hashlib.sha256(STL).hexdigest()


def package(*, legacy=False, changes=None, extra=None):
    artifact = {"kind": "stl", "url": "/artifacts/" + "a" * 16 + "/preview.stl", "sha256": MODEL_HASH}
    if not legacy:
        artifact.update(packagePath="geometry/preview.stl", size=len(STL))
    documents = {
        "manifest.json": {"id": "draft-1", "code": "BEAM-1", "schemaVersion": "3.0", "unitSystem": "mm-kg-s", "coordinateSystem": "right-handed-z-up"},
        "outputs.json": {"success": True, "artifacts": [artifact]},
        "parameters.json": {"definitions": [{"id": "length", "default": 100, "unit": "mm"}]},
        "interfaces.json": {"coordinateSystem": "right-handed-z-up", "interfaces": [{"id": "support", "interfaceType": "locating"}]},
    }
    if changes:
        changes(documents)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, document in documents.items():
            archive.writestr(name, json.dumps(document))
        if not legacy:
            archive.writestr("geometry/preview.stl", STL)
        for name, content in (extra or {}).items():
            entry = zipfile.ZipInfo(name)
            entry.filename = name  # Preserve unsafe names; ZipInfo normalizes Windows separators.
            archive.writestr(entry, content)
    return data.getvalue()


def metadata(data):
    return {"publicationId": "version-1", "templateId": "draft-1", "version": 1, "code": "BEAM-1", "name": "测试横梁", "createdAt": "2026-10-17T00:00:00Z", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "downloadUrl": "https://untrusted.local/package"}


class PreviewTests(unittest.TestCase):
    def client(self, data, *, mutate_metadata=None, model_status=200, model=STL):
        self.requests = []
        self.record = metadata(data)
        if mutate_metadata:
            mutate_metadata(self.record)

        def upstream(request):
            self.requests.append(request)
            self.assertEqual(request.url.host, "parts.local")
            self.assertEqual(request.headers["x-ruiware-api-key"], "server-secret")
            if request.url.path == "/api/public/v1/templates/version-1":
                return httpx.Response(200, json=self.record)
            if request.url.path == "/api/public/v1/templates/version-1/download":
                return httpx.Response(200, content=data)
            if request.url.path == "/artifacts/" + "a" * 16 + "/preview.stl":
                return httpx.Response(model_status, content=model)
            return httpx.Response(404, json={"error": {"code": "REMOTE_TEMPLATE_NOT_FOUND"}})

        return TestClient(create_app(Settings(api_url="http://parts.local", api_key="server-secret"), transport=httpx.MockTransport(upstream), dist_dir=None))

    def test_embedded_model_and_fixed_metadata_cache(self):
        data = package()
        with self.client(data) as client:
            response = client.get("/api/published-templates/version-1/preview")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["publication"]["sha256"], self.record["sha256"])
            self.assertEqual(body["model"]["source"], "package")
            self.assertEqual(body["model"]["sha256"], MODEL_HASH)
            self.assertEqual(body["parameters"][0]["id"], "length")
            self.assertEqual(body["interfaces"][0]["id"], "support")
            self.assertEqual(body["coordinateSystem"], "right-handed-z-up")
            self.assertEqual(response.headers["cache-control"], "no-store")
            model = client.get(body["model"]["url"])
            self.assertEqual(model.status_code, 200)
            self.assertEqual(model.content, STL)
            self.assertEqual(model.headers["x-content-type-options"], "nosniff")
            self.assertNotIn("server-secret", response.text)
            self.assertEqual(client.post("/api/published-templates/version-1/preview").status_code, 405)
        paths = [request.url.path for request in self.requests]
        self.assertEqual(paths.count("/api/public/v1/templates/version-1/download"), 1)
        self.assertEqual(paths.count("/api/public/v1/templates/version-1"), 2)

    def test_package_integrity_and_selected_version(self):
        for mutation, code in [
            (lambda r: r.update(sha256="0" * 64), "PACKAGE_INTEGRITY_ERROR"),
            (lambda r: r.update(size=r["size"] + 1), "PACKAGE_INTEGRITY_ERROR"),
            (lambda r: r.update(publicationId="other"), "LIBRARY_INVALID_RESPONSE"),
        ]:
            with self.subTest(code=code), self.client(package(), mutate_metadata=mutation) as client:
                response = client.get("/api/published-templates/version-1/preview")
                self.assertEqual(response.json(), {"error": {"code": code}})
        with self.client(package()) as client:
            response = client.get("/api/published-templates/version-1/preview?sha256=" + "0" * 64)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(client.get("/api/published-templates/version-1/preview/model").status_code, 422)

    def test_invalid_zip_schema_and_model_contract(self):
        cases = [
            (b"bad zip", "PACKAGE_INVALID"),
            (package(extra={"../escape": "bad"}), "PACKAGE_INVALID"),
            (package(extra={"assets\\escape": "bad"}), "PACKAGE_INVALID"),
            (package(changes=lambda d: d["manifest.json"].update(id="other")), "PACKAGE_INVALID"),
            (package(changes=lambda d: d["manifest.json"].update(schemaVersion="99.0")), "PACKAGE_UNSUPPORTED"),
            (package(changes=lambda d: d["manifest.json"].update(unitSystem="inch")), "PACKAGE_UNSUPPORTED"),
            (package(changes=lambda d: d["outputs.json"].update(success=False)), "MODEL_MISSING"),
            (package(changes=lambda d: d["outputs.json"]["artifacts"][0].update(sha256="0" * 64)), "MODEL_INTEGRITY_ERROR"),
            (package(changes=lambda d: d["outputs.json"]["artifacts"][0].update(size=1)), "MODEL_INTEGRITY_ERROR"),
            (package(changes=lambda d: d["outputs.json"]["artifacts"][0].update(packagePath="../preview.stl")), "PACKAGE_INVALID"),
            (package(changes=lambda d: d["outputs.json"]["artifacts"][0].update(packagePath="geometry/missing.stl")), "PACKAGE_INVALID"),
        ]
        for data, code in cases:
            with self.subTest(code=code), self.client(data) as client:
                response = client.get("/api/published-templates/version-1/preview")
                self.assertEqual(response.status_code, 422 if code in ("PACKAGE_UNSUPPORTED", "MODEL_MISSING") else 502, response.text)
                self.assertEqual(response.json(), {"error": {"code": code}})

    def test_zip_limits_and_cache_eviction(self):
        with patch("backend.app.preview.MAX_JSON_BYTES", 10), self.client(package()) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "PACKAGE_TOO_LARGE")
        with patch("backend.app.preview.MAX_DOWNLOAD_BYTES", 100), self.client(package()) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "PACKAGE_TOO_LARGE")
        with patch("backend.app.preview.MAX_CACHE_BYTES", 1), self.client(package()) as client:
            for _ in range(2):
                self.assertEqual(client.get("/api/published-templates/version-1/preview").status_code, 200)
            self.assertEqual(sum(r.url.path.endswith("/download") for r in self.requests), 2)

    def test_legacy_model_same_origin_and_hash_or_clear_error(self):
        with self.client(package(legacy=True)) as client:
            response = client.get("/api/published-templates/version-1/preview")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["model"]["source"], "legacy")
        with self.client(package(legacy=True), model_status=404) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "MODEL_MISSING")
        with self.client(package(legacy=True), model=b"wrong") as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "MODEL_INTEGRITY_ERROR")
        for url in ["https://evil.local/model.stl", "//evil.local/preview.stl", "/artifacts/../preview.stl", "/api/anything"]:
            data = package(legacy=True, changes=lambda d: d["outputs.json"]["artifacts"][0].update(url=url))
            with self.subTest(url=url), self.client(data) as client:
                self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "MODEL_MISSING")
                self.assertEqual(len(self.requests), 2)

    def test_stream_limit_timeout_redirect_and_retry(self):
        data = package()
        record = metadata(data)
        attempts = 0

        async def upstream(request):
            nonlocal attempts
            if request.url.path.endswith("/download"):
                attempts += 1
                if attempts == 1:
                    await asyncio.sleep(0.1)
                return httpx.Response(200, content=data)
            return httpx.Response(200, json=record)

        app = create_app(Settings(api_url="http://parts.local", api_key="secret"), transport=httpx.MockTransport(upstream), dist_dir=None)
        with TestClient(app) as client:
            with patch("backend.app.preview.DOWNLOAD_TIMEOUT_SECONDS", 0.01):
                self.assertEqual(client.get("/api/published-templates/version-1/preview").status_code, 504)
            self.assertEqual(client.get("/api/published-templates/version-1/preview").status_code, 200)

        calls = []
        def redirected(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://other.local"})
        with TestClient(create_app(Settings(api_url="http://parts.local", api_key="secret"), transport=httpx.MockTransport(redirected), dist_dir=None)) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").status_code, 502)
            self.assertEqual(len(calls), 1)

        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"a" * 101

        def oversized(request):
            return httpx.Response(200, stream=Chunks())
        with patch("backend.app.preview.MAX_JSON_BYTES", 100), TestClient(create_app(Settings(api_url="http://parts.local", api_key="secret"), transport=httpx.MockTransport(oversized), dist_dir=None)) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "PACKAGE_TOO_LARGE")

    def test_duplicate_entries_and_cache_invalidation(self):
        data = package()
        stream = io.BytesIO(data)
        with zipfile.ZipFile(stream, "a") as archive:
            archive.writestr("manifest.json", "{}")
        with self.client(stream.getvalue()) as client:
            self.assertEqual(client.get("/api/published-templates/version-1/preview").json()["error"]["code"], "PACKAGE_INVALID")
        with self.client(data) as client:
            description = client.get("/api/published-templates/version-1/preview").json()
            self.record["sha256"] = "0" * 64
            response = client.get(description["model"]["url"])
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PUBLICATION_CHANGED")

    def test_corrupt_compressed_entry_is_a_safe_package_error(self):
        data = bytearray(package())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            offset = archive.getinfo("manifest.json").header_offset
        name_size, extra_size = struct.unpack_from("<HH", data, offset + 26)
        data[offset + 30 + name_size + extra_size] = 255
        with self.client(bytes(data)) as client:
            response = client.get("/api/published-templates/version-1/preview")
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json(), {"error": {"code": "PACKAGE_INVALID"}})


if __name__ == "__main__":
    unittest.main()
