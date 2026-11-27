"""MCP transport; tools share FastAPI's clients, cache and draft repository."""
import asyncio
import hmac
import ipaddress
import json
from urllib.parse import urlsplit

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse

from ..components.service import ComponentError
from ..library import LibraryError


def tool_result(body: dict, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
                          structuredContent=body, isError=error)


async def invoke(function, *args, **kwargs) -> CallToolResult:
    try:
        if asyncio.iscoroutinefunction(function):
            body = await function(*args, **kwargs)
        else:
            body = await asyncio.to_thread(function, *args, **kwargs)
        if isinstance(body, JSONResponse):
            return tool_result(json.loads(body.body), error=body.status_code >= 400)
        return tool_result(body)
    except ComponentError as error:
        return tool_result(error.as_dict(), error=True)
    except LibraryError as error:
        return tool_result({"error": {"code": error.code}}, error=True)


class MCPAccess:
    def __init__(self, app, owner: FastAPI):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        token = self.owner.state.settings.mcp_token
        if token:
            authorization = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(authorization, ("Bearer " + token).encode("utf-8")):
                response = JSONResponse({"error": {"code": "MCP_UNAUTHORIZED"}}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
                return await response(scope, receive, send)
        else:
            client = scope.get("client")
            try:
                local = bool(client) and ipaddress.ip_address(client[0]).is_loopback
            except ValueError:
                local = False
            if not local:
                return await JSONResponse({"error": {"code": "MCP_LOCAL_ONLY"}}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


def create_mcp(owner: FastAPI, public_base_url: str) -> FastMCP:
    base = urlsplit(public_base_url)
    if base.scheme not in ("http", "https") or not base.hostname or base.username or base.password or base.query or base.fragment or base.path.rstrip("/"):
        raise ValueError("COMPONENT_PUBLIC_BASE_URL must be an http(s) origin")
    mcp = FastMCP(
        "组件模板平台", instructions="查询已发布零部件和组件；创建和编辑与网页共享的组件草稿。修改已有草稿必须使用 expectedRevision。"
        "网页手动编辑优先；Agent 写入使用精确修订检查。冲突后重新读取并核对，不得自动换用最新修订覆盖用户输入。"
        "Agent 修改在网页同步并展示前后差异。支持完整草稿动作、固定版本成员加入、真实CAD贴地和接口对齐、"
        "几何试算/应用/撤销、正式发布及发布库维护。试算不修改草稿，应用需明确确认预览；规则仍仅保存说明，不执行表达式。"
        "发布、清理、恢复会修改共享存储；发布库同步包含自动旧包备份清理，调用前核对用户意图。",
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", base.netloc],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", public_base_url.rstrip("/")],
        ),
    )
    from .component_tools import register as register_components
    from .library_tools import register as register_library
    from .edit_tools import register as register_edits
    from .maintenance_tools import register as register_maintenance
    from .assembly_tools import register as register_assembly
    register_components(mcp, owner, invoke)
    register_library(mcp, owner, invoke)
    register_edits(mcp, owner, invoke)
    register_maintenance(mcp, owner, invoke)
    register_assembly(mcp, owner, invoke)
    return mcp
