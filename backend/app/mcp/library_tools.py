import asyncio
from typing import Annotated
from datetime import datetime

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..library import read_published_library
from ..preview import read_preview, read_publication, SHA256


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_search_templates(query: str = "", limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20,
                                         offset: Annotated[int, Field(strict=True, ge=0)] = 0) -> CallToolResult:
        """按名称或编码搜索已发布零部件版本；offset 为当前发布库范围内的分页偏移。"""
        async def search():
            storage = getattr(app.state, "published_storage", None)
            if storage is not None and storage.client is not None:
                records = await asyncio.to_thread(storage.records, "part")
                library_items = [record.publication for record in records]
                coverage = {"source": "minio", "fetched": len(library_items), "completeLibrary": True}
            else:
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
    async def component_get_template_preview(publicationId: str, sha256: Annotated[str, Field(pattern=SHA256)]) -> CallToolResult:
        """校验固定发布版本并读取参数、原始接口契约及 STL 地址；仅为预览，不加入装配或重建模型。"""
        async def get():
            cached = await read_preview(app.state.http, app.state.settings, app.state.preview_cache, publicationId, sha256,
                                        storage=getattr(app.state, "published_storage", None),
                                        library_cache=getattr(app.state, "library_cache", None))
            body = cached.description.model_dump()
            body["model"]["url"] = app.state.settings.public_base_url.rstrip("/") + body["model"]["url"]
            return body
        return await invoke(get)
