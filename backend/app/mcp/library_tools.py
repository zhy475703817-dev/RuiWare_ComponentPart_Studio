import asyncio
import json
from typing import Annotated, Literal
from datetime import datetime

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field
from fastapi import Request, Response

from ..library import read_published_library
from ..preview import read_preview, read_publication, SHA256
from ..components.service import ComponentError
from ..components.models import ParameterValue


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    publish = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    def absolute_download(body):
        return {**body, "downloadUrl": app.state.settings.public_base_url.rstrip("/") + body["downloadUrl"]}

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_template_authoring_info(publicationId: str, sha256: Annotated[str, Field(pattern=SHA256)],
        parameters: Annotated[dict[str, ParameterValue], Field(max_length=64)] | None = None) -> CallToolResult:
        """选型与装配前读取固定发布版本的公开可调参数、范围、离散规格、原始契约及当前参数下展开的真实接口和诊断。不依赖包内STL，不修改草稿；无效或受保护参数拒绝使用。"""
        from ..reconstruction import reconstruction_info
        return await invoke(reconstruction_info, publicationId, Request({"type": "http", "app": app}), Response(),
                            sha256, json.dumps(parameters or {}, ensure_ascii=False, allow_nan=False))

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
        """读取已发布组件文档、基础检查、qualification验收证据、readiness及随包参考附件和绝对下载地址；可固定SHA-256。旧包缺证据时标为unverified，工程检查未通过不代表模板不可复用。"""
        def get():
            body = app.state.publications.detail(publicationId)
            if sha256 and body["sha256"].lower() != sha256.lower():
                raise ComponentError(409, "PUBLICATION_HASH_MISMATCH")
            return absolute_download(body)
        return await invoke(get)

    @mcp.tool(annotations=publish, structured_output=False)
    async def component_publish_draft(draftId: str,
        expectedRevision: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]) -> CallToolResult:
        """当前修订制作验收通过后正式发布到共享存储；返回独立版本、qualification、readiness、随包附件及下载地址。未验收草稿拒绝发布；模板可复用和实物工程验算是不同状态。"""
        def publish_draft():
            return absolute_download(app.state.publications.publish(draftId, expectedRevision))
        return await invoke(publish_draft)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_template_preview(publicationId: str, sha256: Annotated[str, Field(pattern=SHA256)]) -> CallToolResult:
        """旧式包内或固定STL预览，返回模型地址，不加入装配。制作选型优先用component_get_template_authoring_info读取当前参数和展开接口；本工具依赖预览STL，不代表CAD联动已验证。"""
        async def get():
            cached = await read_preview(app.state.http, app.state.settings, app.state.preview_cache, publicationId, sha256,
                                        storage=getattr(app.state, "published_storage", None),
                                        library_cache=getattr(app.state, "library_cache", None))
            body = cached.description.model_dump()
            body["model"]["url"] = app.state.settings.public_base_url.rstrip("/") + body["model"]["url"]
            return body
        return await invoke(get)
