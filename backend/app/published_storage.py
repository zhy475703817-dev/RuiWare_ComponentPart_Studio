"""Shared MinIO reader for immutable part and component publications."""
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import PurePosixPath
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

    def put_if_absent(self, object_key: str, payload: bytes, content_type: str):
        """Atomic S3 conditional creation; recovery must never overwrite a writer."""
        self.require_write()
        try:
            # MinIO's public put_object does not expose conditional headers.
            # Use its signed single-object request (packages are bounded to 128 MiB).
            self.client._put_object(self.settings.bucket, object_key, payload,
                                    headers={"Content-Type": content_type, "If-None-Match": "*"})
        except Exception as error:
            if getattr(error, 'code', '') in {'PreconditionFailed', 'ConditionalRequestConflict'}:
                raise LibraryError(409, 'BACKUP_TARGET_EXISTS') from None
            raise LibraryError(503, 'CLEANUP_CONDITIONAL_WRITE_FAILED') from None

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

    def delete_part(self, record: StoredPublication, *, expected_sha256: str) -> list[dict]:
        """Delete a part publication only after revalidating its marker and bytes."""
        self.require_write()
        publication = record.publication
        if publication.templateKind != "part":
            raise LibraryError(403, "CLEANUP_KIND_NOT_ALLOWED")
        try:
            current = self.record("part", publication.publicationId, expected_sha256)
            if (current.object_key != record.object_key
                    or current.publication.templateId != publication.templateId
                    or current.publication.version != publication.version):
                raise LibraryError(409, "PUBLICATION_CHANGED")
            package_path = PurePosixPath(current.object_key)
            prefix_parts = PurePosixPath(self.settings.read_prefix).parts
            if ('\\' in current.object_key or ':' in current.object_key or '..' in package_path.parts or
                    package_path.parts[:len(prefix_parts)] != prefix_parts or
                    len(package_path.parts) != len(prefix_parts) + 3 or
                    package_path.name != "template.rwpart" or
                    package_path.parent.name != publication.publicationId):
                raise LibraryError(403, "CLEANUP_KEY_NOT_ALLOWED")

            metadata_key = f"{package_path.parent.as_posix()}/metadata.json"
            versions_by_key = {key: [item for item in self.client.list_objects(
                self.settings.bucket, prefix=key, recursive=True, include_version=True
            ) if item.object_name == key] for key in (current.object_key, metadata_key)}
            if any(not versions for versions in versions_by_key.values()):
                raise LibraryError(409, "PUBLICATION_CHANGED")
            metadata = json.loads(self.read(metadata_key))
            if (metadata.get("templateKind", "part") != "part" or
                    metadata.get("publicationId") != publication.publicationId or
                    metadata.get("templateId") != publication.templateId or
                    metadata.get("version") != publication.version or
                    metadata.get("objectKey") != current.object_key or
                    str(metadata.get("sha256", "")).lower() != expected_sha256.lower()):
                raise LibraryError(409, "PUBLICATION_CHANGED")
            self.package(current)

            deleted_versions = []
            for object_key in (current.object_key, metadata_key):
                deleted_versions.append({'objectKey': object_key,
                                         'versionIds': self._delete_all_versions(object_key, versions_by_key[object_key])})
            return deleted_versions
        except LibraryError:
            raise
        except Exception as error:
            code = getattr(error, "code", "")
            if code in {"AccessDenied", "AccessDeniedByPolicy", "Forbidden"}:
                raise LibraryError(403, "DELETE_PERMISSION_DENIED") from None
            raise LibraryError(503, "REMOTE_STORAGE_DELETE_FAILED") from None

    def _delete_all_versions(self, object_key: str, versions: list) -> list[str | None]:
        prefix = object_key
        try:
            if not versions:
                raise LibraryError(409, "PUBLICATION_CHANGED")
            for item in versions:
                self.client.remove_object(self.settings.bucket, object_key, version_id=getattr(item, "version_id", None))
            remaining = [item for item in self.client.list_objects(
                self.settings.bucket, prefix=prefix, recursive=True, include_version=True
            ) if item.object_name == object_key]
            if remaining:
                raise LibraryError(502, "CLEANUP_DELETE_NOT_VERIFIED")
            return [getattr(item, "version_id", None) for item in versions]
        except LibraryError:
            raise
        except Exception as error:
            code = getattr(error, "code", "")
            if code in {"AccessDenied", "AccessDeniedByPolicy", "Forbidden"}:
                raise LibraryError(403, "DELETE_PERMISSION_DENIED") from None
            raise LibraryError(503, "REMOTE_STORAGE_DELETE_FAILED") from None

    def object_exists(self, object_key: str) -> bool:
        self.require_read()
        try:
            return any(item.object_name == object_key for item in self.client.list_objects(
                self.settings.bucket, prefix=object_key, recursive=True, include_version=True
            ))
        except Exception:
            raise LibraryError(503, "REMOTE_STORAGE_UNAVAILABLE") from None

    def recover_part_after_partial_delete(
        self, record: StoredPublication, *, package: bytes, metadata: bytes,
    ) -> None:
        """Restore only absent objects after a failed two-object deletion.

        Existing bytes must match the verified pre-delete backup. The method
        never overwrites a live object, and refuses recovery if MinIO cannot
        establish that an object is absent (or only has delete markers).
        """
        self.require_write()
        publication = record.publication
        if publication.templateKind != "part":
            raise LibraryError(403, "CLEANUP_KIND_NOT_ALLOWED")
        package_path = PurePosixPath(record.object_key)
        prefix_parts = PurePosixPath(self.settings.read_prefix).parts
        if ("\\" in record.object_key or ":" in record.object_key or ".." in package_path.parts
                or package_path.parts[:len(prefix_parts)] != prefix_parts
                or len(package_path.parts) != len(prefix_parts) + 3
                or package_path.name != "template.rwpart"
                or package_path.parent.name != publication.publicationId):
            raise LibraryError(403, "CLEANUP_KEY_NOT_ALLOWED")
        if hashlib.sha256(package).hexdigest().lower() != publication.sha256.lower():
            raise LibraryError(409, "BACKUP_INTEGRITY_ERROR")
        metadata_key = f"{package_path.parent.as_posix()}/metadata.json"
        try:
            decoded = json.loads(metadata)
        except (ValueError, TypeError):
            raise LibraryError(409, "BACKUP_INTEGRITY_ERROR") from None
        if (decoded.get("templateKind", "part") != "part"
                or decoded.get("publicationId") != publication.publicationId
                or decoded.get("objectKey") != record.object_key
                or str(decoded.get("sha256", "")).lower() != publication.sha256.lower()):
            raise LibraryError(409, "BACKUP_INTEGRITY_ERROR")

        self.restore_objects_if_absent([
            (record.object_key, package, "application/octet-stream"),
            (metadata_key, metadata, "application/json"),
        ])

    def restore_objects_if_absent(self, objects: list[tuple[str, bytes, str]]) -> None:
        # Check the pair before creating either object; conditional writes below
        # still protect against another client arriving after this preflight.
        for object_key, content, _content_type in objects:
            self._restore_target_exists(object_key, content)
        for object_key, content, content_type in objects:
            if not self._restore_target_exists(object_key, content):
                self.put_if_absent(object_key, content, content_type)

    def _restore_target_exists(self, object_key: str, expected: bytes) -> bool:
        try:
            current = self.read(object_key)
        except LibraryError:
            current = None
        if current is not None:
            if current != expected:
                raise LibraryError(409, "BACKUP_TARGET_EXISTS")
            return True

        try:
            versions = [item for item in self.client.list_objects(
                self.settings.bucket, prefix=object_key, recursive=True, include_version=True
            ) if item.object_name == object_key]
        except Exception:
            raise LibraryError(503, "CLEANUP_RECOVERY_UNVERIFIED") from None
        if any(not getattr(item, "is_delete_marker", False) for item in versions):
            raise LibraryError(409, "CLEANUP_RECOVERY_CONFLICT")
        return False
