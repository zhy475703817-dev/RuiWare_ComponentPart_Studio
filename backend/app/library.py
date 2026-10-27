import asyncio
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Request, Response
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import Settings


NonEmptyString = Annotated[str, Field(min_length=1)]
REQUEST_TIMEOUT_SECONDS = 10


class PublishedTemplate(BaseModel):
    model_config = ConfigDict(strict=True)

    publicationId: NonEmptyString
    templateId: NonEmptyString
    version: int = Field(ge=1)
    code: NonEmptyString
    name: NonEmptyString
    createdAt: NonEmptyString
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size: int = Field(ge=0)
    downloadUrl: NonEmptyString
    templateKind: Literal["part", "component"] = "part"
    schemaVersion: str | None = None
    sourceRevision: int | None = None
    engineeringStatus: Literal["incomplete", "ready"] | None = None
    memberCount: int | None = Field(default=None, ge=0)

    @field_validator("publicationId", "templateId", "code", "name", "downloadUrl")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank publication field")
        return value

    @field_validator("createdAt")
    @classmethod
    def validate_date(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class PublishedLibrary(BaseModel):
    model_config = ConfigDict(strict=True)

    items: list[PublishedTemplate]
    count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_records(self) -> "PublishedLibrary":
        ids = [item.publicationId for item in self.items]
        if self.count != len(ids) or len(set(ids)) != len(ids):
            raise ValueError("inconsistent publication count or duplicate id")
        return self


class LibraryError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def upstream_connection(settings: Settings, path: str) -> tuple[httpx.URL, dict[str, str]]:
    api_key = settings.api_key.strip()
    if not api_key:
        raise LibraryError(503, "COMPONENT_LIBRARY_NOT_CONFIGURED")
    if not api_key.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in api_key):
        raise LibraryError(503, "COMPONENT_LIBRARY_INVALID_KEY")
    try:
        base = urlsplit(settings.api_url)
        if base.scheme not in ("http", "https") or not base.hostname or base.username or base.password:
            raise ValueError("invalid upstream address")
        endpoint = httpx.URL(urlunsplit((base.scheme, base.netloc, path, "", "")))
    except (ValueError, httpx.InvalidURL):
        raise LibraryError(503, "COMPONENT_LIBRARY_INVALID_URL") from None
    return endpoint, {"X-RuiWare-API-Key": api_key, "Accept": "application/json"}


def upstream_error(response: httpx.Response) -> None:
    if response.is_redirect:
        raise LibraryError(502, "LIBRARY_UPSTREAM_ERROR")
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        raise LibraryError(502, "LIBRARY_INVALID_RESPONSE") from None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    known = {"PUBLIC_API_NOT_CONFIGURED", "PUBLIC_API_KEY_INVALID", "REMOTE_STORAGE_NOT_CONFIGURED", "REMOTE_STORAGE_UNAVAILABLE", "REMOTE_TEMPLATE_NOT_FOUND", "REMOTE_STORAGE_INTEGRITY_ERROR"}
    if isinstance(code, str) and code in known:
        raise LibraryError(response.status_code, code)
    raise LibraryError(502, "LIBRARY_UPSTREAM_ERROR")


async def read_published_library(client: httpx.AsyncClient, settings: Settings) -> PublishedLibrary:
    endpoint, headers = upstream_connection(settings, "/api/public/v1/templates")

    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            response = await client.get(endpoint, params={"limit": 1000}, headers=headers)
    except (TimeoutError, httpx.TimeoutException):
        raise LibraryError(504, "LIBRARY_TIMEOUT") from None
    except httpx.RequestError:
        raise LibraryError(502, "LIBRARY_UNREACHABLE") from None

    upstream_error(response)
    try:
        body = response.json()
    except ValueError:
        raise LibraryError(502, "LIBRARY_INVALID_RESPONSE") from None
    try:
        return PublishedLibrary.model_validate(body)
    except ValidationError:
        raise LibraryError(502, "LIBRARY_INVALID_RESPONSE") from None


router = APIRouter(prefix="/api", tags=["发布库"])


@router.get("/published-templates", response_model=PublishedLibrary, response_model_exclude_unset=True, summary="读取已发布零部件模板库")
async def published_templates(request: Request, response: Response) -> PublishedLibrary:
    response.headers["Cache-Control"] = "no-store"
    storage = getattr(request.app.state, "published_storage", None)
    if storage is not None and storage.client is not None:
        try:
            records = await asyncio.to_thread(storage.records, "part")
        except LibraryError:
            raise
        return PublishedLibrary(items=[record.publication for record in records], count=len(records))
    return await read_published_library(request.app.state.http, request.app.state.settings)
