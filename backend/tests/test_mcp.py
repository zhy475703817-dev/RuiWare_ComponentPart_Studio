import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.config import MinioSettings, Settings


class MCPTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {
            "COMPONENT_DRAFT_DB": str(Path(directory.name) / "drafts.sqlite3"),
            "COMPONENT_MCP_TOKEN": "",
            "RUIWARE_PUBLIC_API_KEY": "",
            "RUIWARE_MINIO_ENDPOINT": "",
            "RUIWARE_MINIO_ACCESS_KEY": "",
            "RUIWARE_MINIO_SECRET_KEY": "",
            "RUIWARE_MINIO_BUCKET": "",
            "RUIWARE_MINIO_PREFIX": "",
            "RUIWARE_MINIO_READ_PREFIX": "",
            "RUIWARE_MINIO_SECURE": "",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def client(self):
        return TestClient(create_app(dist_dir=None), base_url="http://127.0.0.1:8020", client=("127.0.0.1", 12345))

    def rpc(self, client, method, params=None, **headers):
        return client.post("/mcp", headers={"Accept": "application/json, text/event-stream", **headers}, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
        })

    def call(self, client, name, arguments):
        response = self.rpc(client, "tools/call", {"name": name, "arguments": arguments})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_initialize_discover_and_shared_draft_edit_conflict(self):
        with self.client() as client:
            response = self.rpc(client, "initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "component-test", "version": "1.0"},
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("tools", response.json()["result"]["capabilities"])
            tools = self.rpc(client, "tools/list").json()["result"]["tools"]
            names = {tool["name"] for tool in tools}
            self.assertTrue({"component_get_status", "component_create_draft", "component_list_drafts", "component_set_parameter", "component_get_template_preview", "component_add_mate"} <= names)
            self.assertFalse(any("solve" in name or "publish_draft" in name or "execute_rule" in name for name in names))
            created = self.call(client, "component_create_draft", {"name": "MCP 创建"})
            self.assertFalse(created.get("isError", False))
            draft = created["structuredContent"]
            edited = self.call(client, "component_set_parameter", {
                "draftId": draft["draftId"], "expectedRevision": 1, "parameterId": "componentLength", "value": 3600,
            })
            self.assertEqual(edited["structuredContent"]["document"]["revision"], 2)
            current = client.get(f"/api/component-drafts/{draft['draftId']}").json()
            self.assertEqual(current["document"]["parameters"][0]["value"], 3600)
            stale = self.call(client, "component_set_parameter", {
                "draftId": draft["draftId"], "expectedRevision": 1, "parameterId": "componentLength", "value": 4200,
            })
            self.assertTrue(stale["isError"])
            self.assertEqual(stale["structuredContent"]["error"]["code"], "REVISION_CONFLICT")
            self.assertEqual(client.get(f"/api/component-drafts/{draft['draftId']}").json()["document"]["parameters"][0]["value"], 3600)
            exported = self.call(client, "component_export_draft", {"draftId": draft["draftId"]})["structuredContent"]
            self.assertEqual(exported["stages"]["parameters"]["componentLength"], 3600)

    def test_token_and_local_only_without_token(self):
        with patch.dict(os.environ, {"COMPONENT_MCP_TOKEN": "mcp-only-secret"}), self.client() as client:
            self.assertEqual(self.rpc(client, "tools/list").status_code, 401)
            self.assertEqual(self.rpc(client, "tools/list", Authorization="Bearer incorrect").status_code, 401)
            self.assertEqual(self.rpc(client, "tools/list", Authorization="Bearer mcp-only-secret").status_code, 200)
            self.assertEqual(client.get("/api/health").status_code, 200)
        with TestClient(create_app(dist_dir=None), base_url="http://127.0.0.1:8020", client=("203.0.113.8", 12345)) as client:
            self.assertEqual(self.rpc(client, "tools/list").status_code, 403)

    def test_string_arguments_preserve_json_looking_literals(self):
        with self.client() as client:
            for literal in ["null", "[]", "{}", "true"]:
                draft = self.call(client, "component_create_draft", {"name": literal})["structuredContent"]
                self.assertEqual(draft["document"]["definition"]["name"], literal)
                result = self.call(client, "component_update_definition", {
                    "draftId": draft["draftId"], "expectedRevision": 1, "patch": {"code": literal},
                })
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(result["structuredContent"]["document"]["definition"]["code"], literal)

    def test_member_connection_interface_rule_and_validation_tools(self):
        from backend.tests.test_components import member

        with self.client() as client:
            document = client.post("/api/component-drafts", json={}).json()["document"]
            document["assembly"]["members"] = [member("a"), member("b")]
            draft = self.call(client, "component_create_draft", {"document": document})["structuredContent"]
            draft_id = draft["draftId"]
            edits = [
                ("component_update_member", {"memberId": "a", "patch": {"role": "null", "position": [1, 2, 3], "parameterValues": {"active": False, "length": 3100}}}),
                ("component_add_mate", {"fromMemberId": "a", "fromInterfaceId": "face.left", "toMemberId": "b", "toInterfaceId": "face.left"}),
                ("component_inherit_interface", {"memberId": "a", "interfaceId": "face.left", "name": "对外接口"}),
                ("component_update_rule", {"ruleId": "rule-1", "expression": "[]"}),
            ]
            for name, arguments in edits:
                result = self.call(client, name, {"draftId": draft_id, "expectedRevision": draft["document"]["revision"], **arguments})
                self.assertFalse(result.get("isError", False), result)
                draft = result["structuredContent"]
            self.assertEqual(draft["document"]["assembly"]["members"][0]["role"], "null")
            self.assertIs(draft["document"]["assembly"]["members"][0]["parameterValues"]["active"], False)
            self.assertEqual(draft["document"]["rules"][0]["expression"], "[]")
            checks = self.call(client, "component_validate_draft", {"draftId": draft_id})["structuredContent"]
            self.assertEqual(checks["revision"], 5)
            self.assertEqual(next(c for c in checks["checks"] if c["id"] == "assembly.solve")["status"], "pending")
            removed = self.call(client, "component_remove_member", {"draftId": draft_id, "expectedRevision": 5, "memberId": "a"})["structuredContent"]
            self.assertEqual(removed["document"]["assembly"]["mates"], [])
            self.assertEqual(removed["document"]["ports"], [])
            self.assertEqual(removed["document"]["assembly"]["members"][0]["id"], "b")

    def test_search_scope_safe_upstream_errors_and_preview_url(self):
        from backend.tests.test_preview import package, metadata

        body = package()
        publication = metadata(body)

        def upstream(request):
            if request.url.path.endswith("/download"):
                return httpx.Response(200, content=body)
            if request.url.path.endswith("/templates"):
                earlier = {**publication, "publicationId": "earlier-version", "createdAt": "2026-10-17T01:00:00+08:00"}
                return httpx.Response(200, json={"items": [earlier, publication], "count": 2})
            return httpx.Response(200, json=publication)

        app = create_app(Settings(api_key="upstream-private"), transport=httpx.MockTransport(upstream), dist_dir=None)
        with TestClient(app, base_url="http://127.0.0.1:8020", client=("127.0.0.1", 12345)) as client:
            result = self.call(client, "component_search_templates", {"query": publication["code"], "limit": 10})
            self.assertEqual(result["structuredContent"]["items"][0]["publicationId"], publication["publicationId"])
            self.assertEqual(result["structuredContent"]["coverage"]["upstreamLimit"], 1000)
            details = self.call(client, "component_get_template", {"publicationId": publication["publicationId"], "sha256": publication["sha256"]})
            self.assertEqual(details["structuredContent"]["sha256"], publication["sha256"])
            preview = self.call(client, "component_get_template_preview", {"publicationId": publication["publicationId"], "sha256": publication["sha256"]})
            self.assertFalse(preview.get("isError", False), preview)
            self.assertTrue(preview["structuredContent"]["model"]["url"].startswith("http://127.0.0.1:8020/api/"))
            self.assertNotIn("upstream-private", json.dumps(preview))
        with self.client() as client:
            result = self.call(client, "component_search_templates", {})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["error"]["code"], "COMPONENT_LIBRARY_NOT_CONFIGURED")

    def test_minio_search_details_and_preview_without_parts_platform(self):
        from io import BytesIO
        from backend.tests.storage_helpers import MemoryMinio
        from backend.tests.test_preview import package, metadata

        payload = package()
        publication = metadata(payload)
        storage = MemoryMinio()
        settings = Settings(draft_db=Path(os.environ["COMPONENT_DRAFT_DB"]),
                            minio=MinioSettings(endpoint="minio.test", access_key="test", secret_key="test"))
        directory = "publishers/another-member/" + publication["publicationId"]
        record = {**publication, "objectKey": directory + "/template.rwpart"}
        for key, content in ((record["objectKey"], payload), (directory + "/metadata.json", json.dumps(record).encode())):
            storage.put_object(settings.minio.bucket, key, BytesIO(content), len(content))

        def no_http(request):
            self.fail("MinIO reads must not contact the parts platform")

        with patch("minio.Minio", return_value=storage), TestClient(
            create_app(settings, transport=httpx.MockTransport(no_http), dist_dir=None),
            base_url="http://127.0.0.1:8020", client=("127.0.0.1", 12345),
        ) as client:
            result = self.call(client, "component_search_templates", {"query": publication["code"]})
            self.assertFalse(result.get("isError", False), result)
            self.assertEqual(result["structuredContent"]["coverage"],
                             {"source": "minio", "fetched": 1, "completeLibrary": True})
            args = {"publicationId": publication["publicationId"], "sha256": publication["sha256"]}
            detail = self.call(client, "component_get_template", args)
            self.assertEqual(detail["structuredContent"]["sha256"], publication["sha256"])
            preview = self.call(client, "component_get_template_preview", args)
            self.assertFalse(preview.get("isError", False), preview)
            self.assertEqual(preview["structuredContent"]["model"]["source"], "package")
            self.assertEqual(client.get("/api/published-templates").json()["count"], 1)


if __name__ == "__main__":
    unittest.main()
