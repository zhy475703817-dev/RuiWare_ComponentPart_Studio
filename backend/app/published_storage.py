"""Shared MinIO reader for immutable part and component publications."""
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from urllib.parse import quote

import urllib3
from pydantic import ValidationError

from .config import MinioSettings
from .library import LibraryError, PublishedTemplate


@dataclass(frozen=True)
class StoredPublication:
    publication: PublishedTemplate
    object_key: str


class PublishedStorage:
    """Read and write the one shared publication bucket.

    ``metadata.json`` is the commit marker. A package is not considered
    published until that marker exists, and its object key must stay beside it.
    """

    def __init__(self, settings: MinioSettings, client=None):
        from minio import Minio

        self.settings = settings
        self.client = client
        if self.client is not None:
            return
        if not settings.read_configured:
            return
        try:
            self.client = Minio(
                settings.endpoint,
                access_key=settings.access_key,
                secret_key=settings.secret_key,
                secure=settings.secure,
                http_client=urllib3.PoolManager(
                    timeout=urllib3.Timeout(connect=3, read=10), retries=False
                ),
            )
        except ValueError:
            self.client = None

    @property
    def writable(self) -> bool:
        return (self.client is not None and self.settings.configured
                and self.settings.prefix.startswith(self.settings.read_prefix + "/"))

    def require_read(self):
        if self.client is None:
            raise LibraryError(503, "REMOTE_STORAGE_NOT_CONFIGURED")

    def require_write(self):
        if not self.writable:
            raise LibraryError(503, "REMOTE_STORAGE_NOT_CONFIGURED")

    def read(self, object_key: str) -> bytes:
        self.require_read()
        try:
            response = self.client.get_object(self.settings.bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except LibraryError:
            raise
        except Exception:
            raise LibraryError(503, "REMOTE_STORAGE_UNAVAILABLE") from None

    def put(self, object_key: str, payload: bytes, content_type: str):
        self.require_write()
        try:
            self.client.put_object(
                self.settings.bucket,
                object_key,
                BytesIO(payload),
                length=len(payload),
                content_type=content_type,
            )
        except LibraryError:
            raise
        except Exception:
            raise LibraryError(503, "REMOTE_STORAGE_UNAVAILABLE") from None

    @staticmethod
    def _download_url(kind: str, identifier: str) -> str:
        route = "published-components" if kind == "component" else "published-templates"
        return f"/api/{route}/{quote(identifier, safe='')}/download"

    @staticmethod
    def _publication_identity(publication: PublishedTemplate) -> tuple[object, ...]:
        if publication.templateKind == "component":
            return ("component", "publicationId", publication.publicationId)
        if publication.sourceRevision is None:
            return ("part", "publicationId", publication.publicationId)
        return ("part", publication.code, publication.version, publication.sourceRevision)

    @classmethod
    def _deduplicate(cls, records: list[StoredPublication]) -> list[StoredPublication]:
        unique: list[StoredPublication] = []
        seen: set[tuple[object, ...]] = set()
        for record in sorted(records, key=lambda item: item.publication.createdAt, reverse=True):
            identity = cls._publication_identity(record.publication)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(record)
        return unique

    def records(self, kind: str | None = None, *, include_history: bool = False) -> list[StoredPublication]:
        self.require_read()
        if kind not in (None, "part", "component"):
            raise ValueError("unsupported publication kind")
        items: dict[tuple[str, str], StoredPublication] = {}
        try:
            objects = self.client.list_objects(
                self.settings.bucket,
                prefix=self.settings.read_prefix + "/",
                recursive=True,
            )
            for obj in objects:
                metadata_key = obj.object_name
                if not metadata_key.endswith("/metadata.json"):
                    continue
                metadata = json.loads(self.read(metadata_key))
                if not isinstance(metadata, dict):
                    raise ValueError("invalid metadata")
                template_kind = metadata.get("templateKind", "part")
                if template_kind not in ("part", "component"):
                    raise ValueError("invalid template kind")
                if kind is not None and template_kind != kind:
                    continue
                identifier = metadata["publicationId"]
                if not isinstance(identifier, str) or not identifier or any(
                    char in identifier for char in ("/", "\\")
                ):
                    raise ValueError("invalid publication id")
                directory = metadata_key.removesuffix("/metadata.json")
                object_key = metadata.get("objectKey")
                if directory.rsplit("/", 1)[-1] != identifier or object_key != directory + "/template.rwpart":
                    raise ValueError("invalid object key")
                metadata = {
                    **metadata,
                    "templateKind": template_kind,
                    "downloadUrl": metadata.get("downloadUrl")
                    or self._download_url(template_kind, identifier),
                }
                publication = PublishedTemplate.model_validate(metadata)
                record = StoredPublication(publication, object_key)
                key = template_kind, identifier
                previous = items.get(key)
                if previous is not None and previous.publication.sha256.lower() != publication.sha256.lower():
                    raise ValueError("conflicting publication id")
                items[key] = record
        except LibraryError:
            raise
        except (ValidationError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise LibraryError(502, "REMOTE_STORAGE_INTEGRITY_ERROR") from None
        except Exception:
            raise LibraryError(503, "REMOTE_STORAGE_UNAVAILABLE") from None
        records = sorted(items.values(), key=lambda item: item.publication.createdAt, reverse=True)
        return records if include_history else self._deduplicate(records)

    def record(self, kind: str, publication_id: str, expected_hash: str | None = None) -> StoredPublication:
        if not publication_id or any(char in publication_id for char in ("/", "\\")):
            raise LibraryError(404, "REMOTE_TEMPLATE_NOT_FOUND")
        record = next((item for item in self.records(kind, include_history=True)
                       if item.publication.publicationId == publication_id), None)
        if record is None:
            raise LibraryError(404, "REMOTE_TEMPLATE_NOT_FOUND")
        if expected_hash and record.publication.sha256.lower() != expected_hash.lower():
            raise LibraryError(409, "PUBLICATION_CHANGED")
        return record

    def package(self, record: StoredPublication) -> bytes:
        payload = self.read(record.object_key)
        publication = record.publication
        if (
            len(payload) != publication.size
            or hashlib.sha256(payload).hexdigest().lower() != publication.sha256.lower()
        ):
            raise LibraryError(502, "REMOTE_STORAGE_INTEGRITY_ERROR")
        return payload
