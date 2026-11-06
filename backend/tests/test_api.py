import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings, load_settings
from backend.app.main import create_app


PUBLICATION = {
    "publicationId": "version-1", "templateId": "draft-1", "version": 1,
    "name": "横梁", "code": "BEAM-001", "createdAt": "2026-10-05T00:00:00Z",
    "sha256": "a" * 64, "size": 1024,
    "downloadUrl": "/api/public/v1/templates/version-1/download",
}


class BackendTests(unittest.TestCase):
    def client(self, handler=None, **settings):
        app = create_app(
            Settings(**{"api_url": "http://parts.local/base", "api_key": "server-only-secret", **settings}),
            transport=httpx.MockTransport(handler or (lambda _: httpx.Response(200, json={"items": [], "count": 0}))),
            dist_dir=None,
        )
        return TestClient(app)

    def test_health_openapi_and_read_only_route(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/health").json(), {"status": "ok", "service": "component-template-api"})
            self.assertEqual(client.get("/docs").status_code, 200)
            spec = client.get("/openapi.json").json()
            self.assertIn("/api/published-templates", spec["paths"])
            self.assertIn("PublishedLibrary", spec["components"]["schemas"])
            self.assertEqual(client.post("/api/published-templates").status_code, 405)
            self.assertEqual(client.get("/api/unknown").status_code, 404)
            self.assertNotIn("server-only-secret", json.dumps(spec))

    def test_fixed_upstream_path_key_and_all_versions(self):
        requests = []
        second = {**PUBLICATION, "publicationId": "version-2", "version": 2}

        def upstream(request):
            requests.append(request)
            return httpx.Response(200, json={"items": [{**PUBLICATION, "internalDetail": "private"}, second], "count": 2, "internalKey": "private"})

        with self.client(upstream) as client:
            response = client.get("/api/published-templates?apiKey=attacker&limit=1&url=http://other.local")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"], [PUBLICATION, second])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("server-only-secret", response.text)
            self.assertNotIn("x-ruiware-api-key", response.headers)
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "http://parts.local/api/public/v1/templates?limit=1000")
        self.assertEqual(requests[0].headers["x-ruiware-api-key"], "server-only-secret")
        self.assertEqual(requests[0].extensions["timeout"]["read"], 10)

    def test_configuration_errors_do_not_contact_upstream(self):
        def unexpected(_):
            self.fail("invalid configuration must not contact upstream")

        for overrides, code in [
            ({"api_key": " "}, "COMPONENT_LIBRARY_NOT_CONFIGURED"),
            *[({"api_key": key}, "COMPONENT_LIBRARY_INVALID_KEY") for key in ["中文占位", "secret\r\nheader", "secret\x00"]],
            *[({"api_url": url}, "COMPONENT_LIBRARY_INVALID_URL") for url in ["bad", "file:///tmp/a", "http://user:secret@host", "http://"]],
        ]:
            with self.subTest(overrides=overrides), self.client(unexpected, **overrides) as client:
                response = client.get("/api/published-templates")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": code}})

    def test_upstream_errors_are_sanitized(self):
        for status, payload, expected_status, code in [
            (401, {"error": {"code": "PUBLIC_API_KEY_INVALID", "detail": "private"}}, 401, "PUBLIC_API_KEY_INVALID"),
            (503, {"error": {"code": "REMOTE_STORAGE_NOT_CONFIGURED"}}, 503, "REMOTE_STORAGE_NOT_CONFIGURED"),
            (503, {"error": {"code": "REMOTE_STORAGE_UNAVAILABLE"}}, 503, "REMOTE_STORAGE_UNAVAILABLE"),
            (503, {"error": {"code": "PUBLIC_API_NOT_CONFIGURED"}}, 503, "PUBLIC_API_NOT_CONFIGURED"),
            (500, {"error": {"code": "UNKNOWN", "detail": "private"}}, 502, "LIBRARY_UPSTREAM_ERROR"),
            (500, [], 502, "LIBRARY_UPSTREAM_ERROR"),
            (500, {"error": {"code": ["bad"]}}, 502, "LIBRARY_UPSTREAM_ERROR"),
        ]:
            with self.subTest(status=status, payload=payload), self.client(lambda _: httpx.Response(status, json=payload)) as client:
                response = client.get("/api/published-templates")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json(), {"error": {"code": code}})
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_network_timeout_redirect_and_invalid_json(self):
        for error, status, code in [
            (httpx.ConnectError("secret debug info"), 502, "LIBRARY_UNREACHABLE"),
            (httpx.ReadTimeout("secret debug info"), 504, "LIBRARY_TIMEOUT"),
        ]:
            def failed(_, cause=error):
                raise cause
            with self.subTest(error=error), self.client(failed) as client:
                response = client.get("/api/published-templates")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"error": {"code": code}})

        requests = []
        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "http://other.local/secret"})
        with self.client(redirect) as client:
            self.assertEqual(client.get("/api/published-templates").status_code, 502)
        self.assertEqual(len(requests), 1)
        with self.client(lambda _: httpx.Response(200, text="<html>private</html>")) as client:
            response = client.get("/api/published-templates")
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["error"]["code"], "LIBRARY_INVALID_RESPONSE")

    def test_invalid_publication_metadata_and_empty_list(self):
        invalid = [
            {}, {"items": [PUBLICATION], "count": 0},
            {"items": [PUBLICATION, PUBLICATION], "count": 2},
            *[{"items": [{**PUBLICATION, **override}], "count": 1} for override in [
                {"publicationId": " "}, {"version": 0}, {"version": True}, {"size": -1},
                {"sha256": "bad"}, {"createdAt": "bad"}, {"downloadUrl": ""},
            ]],
        ]
        for body in invalid:
            with self.subTest(body=body), self.client(lambda _: httpx.Response(200, json=body)) as client:
                response = client.get("/api/published-templates")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json(), {"error": {"code": "LIBRARY_INVALID_RESPONSE"}})
        with self.client() as client:
            self.assertEqual(client.get("/api/published-templates").json(), {"items": [], "count": 0})

    def test_retry_after_timeout_uses_same_application(self):
        attempts = 0

        def upstream(_):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("private")
            return httpx.Response(200, json={"items": [PUBLICATION], "count": 1})

        with self.client(upstream) as client:
            self.assertEqual(client.get("/api/published-templates").status_code, 504)
            response = client.get("/api/published-templates")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["count"], 1)

    def test_whole_request_deadline_and_recovery(self):
        attempts = 0

        async def upstream(_):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await asyncio.sleep(0.1)
            return httpx.Response(200, json={"items": [], "count": 0})

        with patch("backend.app.library.REQUEST_TIMEOUT_SECONDS", 0.01), self.client(upstream) as client:
            response = client.get("/api/published-templates")
            self.assertEqual(response.status_code, 504)
            self.assertEqual(response.json(), {"error": {"code": "LIBRARY_TIMEOUT"}})
            self.assertEqual(client.get("/api/published-templates").status_code, 200)

    def test_settings_files_and_environment_precedence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("RUIWARE_PUBLIC_API_KEY=base\nRUIWARE_TEMPLATE_API_URL=http://base\n", encoding="utf-8")
            (root / ".env.local").write_text("RUIWARE_PUBLIC_API_KEY=local\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(load_settings(root).api_key, "local")
                self.assertEqual(load_settings(root).api_url, "http://base")
            with patch.dict("os.environ", {"RUIWARE_PUBLIC_API_KEY": "environment"}, clear=True):
                self.assertEqual(load_settings(root).api_key, "environment")
                self.assertNotIn("environment", repr(load_settings(root)))
            with patch.dict("os.environ", {"RUIWARE_PUBLIC_API_KEY": ""}, clear=True):
                self.assertEqual(load_settings(root).api_key, "")

    def test_production_build_files_and_api_404(self):
        with TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / "index.html").write_text("<html>component-workbench</html>", encoding="utf-8")
            (dist / "assets").mkdir()
            (dist / "assets" / "app.js").write_text("// client asset", encoding="utf-8")
            app = create_app(Settings(), dist_dir=dist)
            with TestClient(app) as client:
                self.assertIn("component-workbench", client.get("/").text)
                self.assertEqual(client.get("/assets/app.js").status_code, 200)
                self.assertEqual(client.get("/api/unknown").status_code, 404)
                self.assertEqual(client.get("/.env.local").status_code, 404)
                self.assertEqual(client.get("/docs").status_code, 200)


if __name__ == "__main__":
    unittest.main()
