"""Exercise the real official SDK client against a temporary HTTP server."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MCPHTTPTests(unittest.TestCase):
    def test_sdk_client_and_rest_share_persisted_draft_over_http(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with TemporaryDirectory() as directory:
            environment = {**os.environ, "COMPONENT_DRAFT_DB": str(Path(directory) / "drafts.sqlite3"),
                           "COMPONENT_MCP_TOKEN": "temporary-test-token", "COMPONENT_PUBLIC_BASE_URL": base,
                           "RUIWARE_PUBLIC_API_KEY": ""}
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
                                        "--port", str(port), "--log-level", "error"],
                                       cwd=Path(__file__).resolve().parents[2], env=environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            self.fail("temporary MCP server exited before readiness")
                        try:
                            if client.get(base + "/api/health").status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail("temporary MCP server did not become ready")
                    self.assertEqual(client.post(base + "/mcp", json={}).status_code, 401)
                asyncio.run(self.exercise(base))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    async def exercise(self, base):
        async with httpx.AsyncClient(headers={"Authorization": "Bearer temporary-test-token"}, trust_env=False) as transport:
            async with streamable_http_client(base + "/mcp", http_client=transport) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn("component_create_draft", {tool.name for tool in tools.tools})
                    result = await session.call_tool("component_create_draft", {"name": "HTTP MCP 草稿"})
                    self.assertFalse(result.isError)
                    draft = result.structuredContent
                    edited = await session.call_tool("component_update_definition", {
                        "draftId": draft["draftId"], "expectedRevision": 1, "patch": {"name": "AI 已修改"},
                    })
                    self.assertFalse(edited.isError)
                    response = await transport.get(base + f"/api/component-drafts/{draft['draftId']}")
                    self.assertEqual(response.json()["document"]["definition"]["name"], "AI 已修改")
                    self.assertEqual(response.json()["changes"][0]["actor"], "agent")
                    # The GUI still has R1: its name wins while unrelated Agent edits remain.
                    web_edit = await transport.post(base + f"/api/component-drafts/{draft['draftId']}/actions", json={
                        "expectedRevision": 1, "action": {"type": "definition.patch", "patch": {"name": "用户优先"}},
                    })
                    self.assertEqual(web_edit.status_code, 200)
                    self.assertTrue(web_edit.json()["reconciled"])
                    self.assertEqual(web_edit.json()["document"]["definition"]["name"], "用户优先")
                    stale_agent = await session.call_tool("component_update_definition", {
                        "draftId": draft["draftId"], "expectedRevision": 2, "patch": {"name": "不能覆盖用户"},
                    })
                    self.assertTrue(stale_agent.isError)
                    self.assertEqual(stale_agent.structuredContent["error"]["code"], "REVISION_CONFLICT")
                    web_edit = await transport.post(base + f"/api/component-drafts/{draft['draftId']}/actions", json={
                        "expectedRevision": 3, "action": {"type": "parameter.value", "id": "componentLength", "value": 4100},
                    })
                    self.assertEqual(web_edit.status_code, 200)
                    reread = await session.call_tool("component_get_draft", {"draftId": draft["draftId"]})
                    self.assertEqual(reread.structuredContent["document"]["parameters"][0]["value"], 4100)
                    self.assertEqual(reread.structuredContent["document"]["revision"], 4)


if __name__ == "__main__":
    unittest.main()
