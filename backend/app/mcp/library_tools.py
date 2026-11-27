import asyncio
from typing import Annotated, Literal
from datetime import datetime

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..library import read_published_library
from ..preview import read_preview, read_publication, SHA256
from ..components.service import ComponentError


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    publish = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    def absolute_download(body):
        return {**body, "downloadUrl": app.state.settings.public_base_url.rstrip("/") + body["downloadUrl"]}

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_search_templates(query: str = "", limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20,
                                         offset: Annotated[int, Field(strict=True, ge=0)] = 0,
                                         templateKind: Literal["part", "component", "all"] = "part") -> CallToolResult:
        """按名称或编码搜索发布版本；templateKind选择零部件、组件或全部，默认零部件。"""
        async def search():
            storage = getattr(app.state, "published_storage", None)
            if storage is not None and storage.client is not None:
                records = await asyncio.to_thread(storage.records, None if templateKind == "all" else templateKind)
                library_items = [record.publication for record in records]
                coverage = {"source": "minio", "fetched": len(library_items), "completeLibrary": True}
            else:
                if templateKind != "part":
                    raise ComponentError(503, "REMOTE_STORAGE_NOT_CONFIGURED")
                library = await read_published_library(app.state.http, app.state.settings)
                library_items = library.items
                coverage = {"source": "legacy-http", "upstreamLimit": 1000,
                            "fetched": library.count, "completeLibrary": False}
            terms = query.strip().casefold()
            items = [item for item in library_items if terms in item.name.casefold() or terms in item.code.casefold()]
            items.sort(key=lambda item: datetime.fromisoformat(item.createdAt.replace("Z", "+00:00")).timestamp(), reverse=True)
            return {"items": [item.model_dump() for item in items[offset:offset + limit]], "count": len(items),
                    "offset": offset, "limit": limit,
                    "coverage": coverage}
        return await invoke(search)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_template(publicationId: str, sha256: Annotated[str, Field(pattern=r"^(?:[a-fA-F0-9]{64})?$")] = "") -> CallToolResult:
        """读取指定发布版本元数据；可传 sha256 检查版本是否变化。不下载模型包。"""
        async def get():
            storage = getattr(app.state, "published_storage", None)
            if storage is not None and storage.client is not None:
                record = await asyncio.to_thread(storage.record, "part", publicationId, sha256 or None)
                return record.publication.model_dump()
            record = await read_publication(app.state.http, app.state.settings, publicationId, sha256 or None)
            return record.model_dump()
        return await invoke(get)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_list_publications() -> CallToolResult:
        """列出共享发布库中的组件版本；零部件用component_search_templates。"""
        return await invoke(app.state.publications.list)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_publication(publicationId: str,
        sha256: Annotated[str, Field(pattern=r"^(?:[a-fA-F0-9]{64})?$")] = "") -> CallToolResult:
        """读取已发布组件文档、检查和绝对下载地址；可固定SHA-256校验版本。"""
        def get():
            body = app.state.publications.detail(publicationId)
            if sha256 and body["sha256"].lower() != sha256.lower():
                raise ComponentError(409, "PUBLICATION_HASH_MISMATCH")
            return absolute_download(body)
        return await invoke(get)

    @mcp.tool(annotations=publish, structured_output=False)
    async def component_publish_draft(draftId: str,
        expectedRevision: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]) -> CallToolResult:
        """将指定修订正式发布到共享存储；创建独立发布版本，并返回包下载地址。"""
        def publish_draft():
            return absolute_download(app.state.publications.publish(draftId, expectedRevision))
        return await invoke(publish_draft)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_template_preview(publicationId: str, sha256: Annotated[str, Field(pattern=SHA256)]) -> CallToolResult:
        """AI选型时先读取固定发布版本的真实参数和接口契约，确认可调范围及连接来源再装配；返回STL预览地址，不加入装配，不代表CAD联动已验证。"""
        async def get():
            cached = await read_preview(app.state.http, app.state.settings, app.state.preview_cache, publicationId, sha256,
                                        storage=getattr(app.state, "published_storage", None),
                                        library_cache=getattr(app.state, "library_cache", None))
            body = cached.description.model_dump()
            body["model"]["url"] = app.state.settings.public_base_url.rstrip("/") + body["model"]["url"]
            return body
        return await invoke(get)
