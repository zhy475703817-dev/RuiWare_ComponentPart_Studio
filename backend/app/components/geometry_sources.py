"""Load pinned CAD inputs for both resizing and assembly operations."""
import asyncio

from fastapi import Request

from .resize_solver import ResizeFailure


async def load_sources(app, document, cancelled=lambda: False):
    from ..reconstruction import _read_template

    request = Request({"type": "http", "app": app})
    templates, publications = {}, {}

    async def collect(document, ancestors=()):
        if len(ancestors) > 16:
            raise ResizeFailure("组件嵌套超过16层", "RESIZE_NESTING")
        for member in document["assembly"]["members"]:
            if cancelled():
                raise ResizeFailure("已取消", "RESIZE_CANCELLED")
            reference = member["template"]
            identity = (reference["publicationId"], reference["sha256"])
            if reference.get("templateKind") == "component":
                if identity in ancestors:
                    raise ResizeFailure("组件循环引用", "RESIZE_NESTING")
                publication = await asyncio.to_thread(app.state.publications.detail, reference["publicationId"])
                if any(publication[k] != reference[k] for k in ("templateId", "version")) or publication["sha256"].lower() != reference["sha256"].lower():
                    raise ResizeFailure("子组件发布版本不一致", "RESIZE_REFERENCE")
                publications[identity] = publication["document"]
                await collect(member.get("instanceDocument") or publication["document"], (*ancestors, identity))
            elif identity not in templates:
                publication, draft, material = await _read_template(request, *identity)
                if publication.templateId != reference["templateId"] or publication.version != reference["version"]:
                    raise ResizeFailure("零件发布版本不一致", "RESIZE_REFERENCE")
                templates[identity] = draft, material

    await collect(document)
    return templates, publications
