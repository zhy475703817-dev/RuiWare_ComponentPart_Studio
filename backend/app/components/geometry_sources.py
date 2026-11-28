"""Load pinned CAD inputs for both resizing and assembly operations."""
import asyncio

from ..library import LibraryError
from .geometry_common import ResizeFailure, template_key


async def load_sources(app, document, cancelled=lambda: False, *, resource=None):
    from ..reconstruction import _read_template

    templates, publications, loaded = {}, {}, {}

    async def fetch(key, loader):
        if key not in loaded:
            loaded[key] = await resource(key, loader) if resource is not None else await loader()
        return loaded[key]

    async def collect(document, ancestors=()):
        if len(ancestors) > 16:
            raise ResizeFailure("组件嵌套超过16层", "RESIZE_NESTING")
        for member in document["assembly"]["members"]:
            if cancelled():
                raise ResizeFailure("已取消", "RESIZE_CANCELLED")
            reference = member["template"]
            identity = template_key(reference)
            expected = reference["templateId"], reference["version"]
            if reference.get("templateKind") == "component":
                if identity in ancestors:
                    raise ResizeFailure("组件循环引用", "RESIZE_NESTING")
                publication = await fetch(("component", *identity), lambda id=identity[0]: asyncio.to_thread(app.state.publications.detail, id))
                if (publication["templateId"], publication["version"]) != expected or publication["sha256"].lower() != identity[1]:
                    raise ResizeFailure("子组件发布版本不一致", "RESIZE_REFERENCE")
                publications[identity] = publication["document"]
                await collect(member.get("instanceDocument") or publications[identity], (*ancestors, identity))
            else:
                try:
                    publication, draft, material = await fetch(("part", *identity), lambda pin=identity: _read_template(app.state, *pin))
                except LibraryError as error:
                    raise ResizeFailure(f"无法读取 {member.get('name', member['id'])} 的固定发布来源：{error.code}",
                                        error.code, [{"memberId": member["id"], "publicationId": identity[0],
                                                      "sha256": reference["sha256"], "code": error.code}]) from error
                if (publication.templateId, publication.version) != expected:
                    raise ResizeFailure("零件发布版本不一致", "RESIZE_REFERENCE")
                templates[identity] = draft, material

    await collect(document)
    return templates, publications
