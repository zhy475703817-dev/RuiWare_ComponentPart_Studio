import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
from typing import Any, Literal
from urllib.parse import quote
import zipfile
import zlib

from fastapi import APIRouter, Query, Request, Response
import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings
from .library import LibraryError, PublishedTemplate, upstream_connection, upstream_error


MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
SHA256 = r"^[a-fA-F0-9]{64}$"


class PreviewModel(BaseModel):
    format: Literal["stl"] = "stl"
    source: Literal["package", "legacy"]
    url: str
    sha256: str
    size: int


class TemplatePreview(BaseModel):
    publication: PublishedTemplate
    unitSystem: Literal["mm-kg-s"] = "mm-kg-s"
    coordinateSystem: Literal["right-handed-z-up"] = "right-handed-z-up"
    parameters: list[dict[str, Any]]
    interfaces: list[dict[str, Any]]
    model: PreviewModel


@dataclass
class CachedPreview:
    description: TemplatePreview
    model: bytes
    weight: int


class PreviewCache:
    def __init__(self):
        self.entries: OrderedDict[tuple[str, str], CachedPreview] = OrderedDict()
        self.size = 0
        self.slots = asyncio.Semaphore(2)

    def get(self, key: tuple[str, str]) -> CachedPreview | None:
        value = self.entries.get(key)
        if value is not None:
            self.entries.move_to_end(key)
        return value

    def put(self, key: tuple[str, str], value: CachedPreview):
        if value.weight > MAX_CACHE_BYTES:
            return
        while self.entries and self.size + value.weight > MAX_CACHE_BYTES:
            _, removed = self.entries.popitem(last=False)
            self.size -= removed.weight
        self.entries[key] = value
        self.size += value.weight


async def download(client: httpx.AsyncClient, settings: Settings, path: str, limit: int, *, legacy=False) -> bytes:
    endpoint, headers = upstream_connection(settings, path)
    async with client.stream("GET", endpoint, headers=headers) as response:
        if legacy and response.status_code == 404:
            raise LibraryError(422, "MODEL_MISSING")
        if not response.is_success:
            # Bound error bodies too: no upstream debug text is returned.
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_JSON_BYTES:
                    raise LibraryError(502, "LIBRARY_UPSTREAM_ERROR")
                content.extend(chunk)
            upstream_error(httpx.Response(response.status_code, content=bytes(content)))
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise LibraryError(502, "PACKAGE_TOO_LARGE")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > limit:
                raise LibraryError(502, "PACKAGE_TOO_LARGE")
            content.extend(chunk)
        return bytes(content)


def read_package(data: bytes, publication: PublishedTemplate, *, require_model: bool = True) -> tuple[list[dict], list[dict], dict | None, bytes | None]:
    if len(data) != publication.size or hashlib.sha256(data).hexdigest() != publication.sha256.lower():
        raise LibraryError(502, "PACKAGE_INTEGRITY_ERROR")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise LibraryError(502, "PACKAGE_TOO_LARGE")
            names: set[str] = set()
            for item in entries:
                path = PurePosixPath(item.filename)
                if (item.filename in names or item.filename.startswith("/") or "\\" in item.orig_filename or
                    ":" in item.filename or ".." in path.parts or "\0" in item.filename or
                    stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1 or
                    item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise LibraryError(502, "PACKAGE_INVALID")
                names.add(item.filename)

            def document(name: str) -> dict:
                if archive.getinfo(name).file_size > MAX_JSON_BYTES:
                    raise LibraryError(502, "PACKAGE_TOO_LARGE")
                body = json.loads(archive.read(name), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")))
                if not isinstance(body, dict):
                    raise ValueError("invalid document")
                return body

            manifest = document("manifest.json")
            if manifest.get("schemaVersion") != "3.0" or manifest.get("unitSystem") != "mm-kg-s" or manifest.get("coordinateSystem") != "right-handed-z-up":
                raise LibraryError(422, "PACKAGE_UNSUPPORTED")
            if manifest.get("id") != publication.templateId or manifest.get("code") != publication.code:
                raise ValueError("publication identity mismatch")
            parameters, interfaces = document("parameters.json").get("definitions"), document("interfaces.json")
            if interfaces.get("coordinateSystem") != manifest["coordinateSystem"]:
                raise ValueError("interface coordinates mismatch")
            interfaces = interfaces.get("interfaces")
            if any(not isinstance(items, list) or not all(isinstance(item, dict) for item in items) for items in [parameters, interfaces]):
                raise ValueError("invalid definitions")
            if not require_model:
                return parameters, interfaces, None, None
            outputs = document("outputs.json")
            artifacts = outputs.get("artifacts")
            if not isinstance(artifacts, list) or (require_model and outputs.get("success") is not True):
                if not require_model:
                    return parameters, interfaces, None, None
                raise LibraryError(422, "MODEL_MISSING")
            models = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == "stl"]
            if len(models) != 1:
                if not require_model:
                    return parameters, interfaces, None, None
                raise LibraryError(422, "MODEL_MISSING")
            model = models[0]
            if not isinstance(model.get("sha256"), str) or not re.fullmatch(SHA256, model["sha256"]):
                raise ValueError("invalid model hash")
            if "packagePath" not in model:
                return parameters, interfaces, model, None
            if model["packagePath"] != "geometry/preview.stl":
                raise ValueError("invalid model path")
            if archive.getinfo(model["packagePath"]).file_size > MAX_DOWNLOAD_BYTES:
                raise LibraryError(502, "PACKAGE_TOO_LARGE")
            body = archive.read(model["packagePath"])
            if type(model.get("size")) is not int or len(body) != model["size"]:
                raise LibraryError(502, "MODEL_INTEGRITY_ERROR")
            return parameters, interfaces, model, body
    except (zipfile.BadZipFile, zlib.error, EOFError, KeyError, ValueError, TypeError, RuntimeError, NotImplementedError, OverflowError, RecursionError):
        raise LibraryError(502, "PACKAGE_INVALID") from None


async def read_publication(client: httpx.AsyncClient, settings: Settings, publication_id: str, expected_hash: str | None = None) -> PublishedTemplate:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", publication_id):
        raise LibraryError(404, "REMOTE_TEMPLATE_NOT_FOUND")
    prefix = "/api/public/v1/templates/" + quote(publication_id, safe="")
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            metadata = await download(client, settings, prefix, MAX_JSON_BYTES)
            try:
                publication = PublishedTemplate.model_validate_json(metadata)
            except ValidationError:
                raise LibraryError(502, "LIBRARY_INVALID_RESPONSE") from None
            if publication.publicationId != publication_id:
                raise LibraryError(502, "LIBRARY_INVALID_RESPONSE")
            if expected_hash and publication.sha256.lower() != expected_hash.lower():
                raise LibraryError(409, "PUBLICATION_CHANGED")
            return publication
    except (TimeoutError, httpx.TimeoutException):
        raise LibraryError(504, "LIBRARY_TIMEOUT") from None
    except httpx.RequestError:
        raise LibraryError(502, "LIBRARY_UNREACHABLE") from None


async def read_preview(client: httpx.AsyncClient, settings: Settings, cache: PreviewCache,
                       publication_id: str, expected_hash: str | None, *, storage=None, library_cache=None) -> CachedPreview:
    prefix = "/api/public/v1/templates/" + quote(publication_id, safe="")
    # The SQLite package cache is tied to the shared MinIO publication source.
    # When MinIO is not configured, keep the legacy HTTP path fully isolated
    # from any historical cache rows left by an earlier configuration.
    cache_enabled = storage is not None and storage.client is not None and library_cache is not None
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            publication = library_cache.publication(publication_id, expected_hash) if cache_enabled else None
            record = None
            if publication is None and storage is not None and storage.client is not None:
                record = await asyncio.to_thread(storage.record, "part", publication_id, expected_hash)
                publication = record.publication
            if publication is None:
                publication = await read_publication(client, settings, publication_id, expected_hash)
            key = publication_id, publication.sha256.lower()
            async with cache.slots:
                cached = cache.get(key)
                if cached:
                    return cached
                if publication.size > MAX_DOWNLOAD_BYTES:
                    raise LibraryError(502, "PACKAGE_TOO_LARGE")
                body = library_cache.package(publication) if cache_enabled else None
                if body is None:
                    if record is None and storage is not None and storage.client is not None:
                        record = await asyncio.to_thread(storage.record, "part", publication_id, publication.sha256)
                    if record is not None:
                        body = await asyncio.to_thread(storage.package, record)
                    else:
                        body = await download(client, settings, prefix + "/download", MAX_DOWNLOAD_BYTES)
                    if cache_enabled:
                        library_cache.save_package(publication, body)
                parameters, interfaces, model, stl = await asyncio.to_thread(read_package, body, publication)
                source = "package"
                if stl is None:
                    source = "legacy"
                    url = model.get("url")
                    if not isinstance(url, str) or not re.fullmatch(r"/artifacts/[a-fA-F0-9]{16,64}/preview\.stl", url):
                        raise LibraryError(422, "MODEL_MISSING")
                    stl = await download(client, settings, url, MAX_DOWNLOAD_BYTES, legacy=True)
                if not stl or hashlib.sha256(stl).hexdigest() != model["sha256"].lower():
                    raise LibraryError(502, "MODEL_INTEGRITY_ERROR")
                model_url = f"/api/published-templates/{quote(publication_id, safe='')}/preview/model?sha256={publication.sha256.lower()}"
                description = TemplatePreview(publication=publication, parameters=parameters, interfaces=interfaces,
                    model=PreviewModel(source=source, url=model_url, sha256=model["sha256"].lower(), size=len(stl)))
                result = CachedPreview(description, stl, len(stl) + len(description.model_dump_json().encode()))
                # Another concurrent request may have filled the same cache entry.
                if cache.get(key) is None:
                    cache.put(key, result)
                return result
    except (TimeoutError, httpx.TimeoutException):
        raise LibraryError(504, "LIBRARY_TIMEOUT") from None
    except httpx.RequestError:
        raise LibraryError(502, "LIBRARY_UNREACHABLE") from None


async def load_preview(request: Request, publication_id: str, expected_hash: str | None) -> CachedPreview:
    return await read_preview(request.app.state.http, request.app.state.settings, request.app.state.preview_cache,
                              publication_id, expected_hash, storage=getattr(request.app.state, "published_storage", None),
                              library_cache=getattr(request.app.state, "library_cache", None))


router = APIRouter(prefix="/api/published-templates", tags=["模型预览"])


@router.get("/{publication_id}/preview", response_model=TemplatePreview, summary="校验并读取发布包预览信息")
async def preview(publication_id: str, request: Request, response: Response, sha256: str | None = Query(default=None, pattern=SHA256)):
    result = await load_preview(request, publication_id, sha256)
    response.headers["Cache-Control"] = "no-store"
    return result.description


@router.get("/{publication_id}/preview/model", summary="读取指定发布包的已校验 STL 模型")
async def preview_model(publication_id: str, request: Request, sha256: str = Query(pattern=SHA256)):
    result = await load_preview(request, publication_id, sha256)
    return Response(result.model, media_type="model/stl", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-RuiWare-SHA256": result.description.model.sha256})
