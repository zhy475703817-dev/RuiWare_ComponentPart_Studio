"""Shared component releases. SQLite snapshots are retries, never the public library."""
from functools import wraps
import hashlib
from io import BytesIO
import json
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError
import urllib3

from ..config import MinioSettings
from ..library import PublishedTemplate
from .models import ComponentDocument
from .repository import DraftRepository
from .service import ComponentError


def storage_errors(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ComponentError:
            raise
        except Exception:
            # Never return SDK messages containing server or credential details.
            raise ComponentError(503, "REMOTE_STORAGE_UNAVAILABLE") from None
    return call


class ComponentPublications:
    def __init__(self, drafts: DraftRepository, settings: MinioSettings):
        from minio import Minio

        self.drafts = drafts
        self.settings = settings
        self.client = None
        if settings.configured:
            if not settings.prefix.startswith(settings.read_prefix + "/"):
                return
            try:
                self.client = Minio(settings.endpoint, access_key=settings.access_key, secret_key=settings.secret_key,
                                    secure=settings.secure, http_client=urllib3.PoolManager(
                                        timeout=urllib3.Timeout(connect=3, read=10), retries=False))
            except ValueError:
                # A malformed storage address must not prevent draft editing.
                self.client = None

    def _require_storage(self):
        if self.client is None:
            raise ComponentError(503, "REMOTE_STORAGE_NOT_CONFIGURED")

    def _read(self, key: str) -> bytes:
        response = self.client.get_object(self.settings.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _put(self, key: str, payload: bytes, content_type: str):
        self.client.put_object(self.settings.bucket, key, BytesIO(payload), length=len(payload), content_type=content_type)

    @staticmethod
    def _public(metadata: dict) -> dict:
        return PublishedTemplate.model_validate(metadata).model_dump(exclude_unset=True)

    def _metadata(self) -> list[dict]:
        self._require_storage()
        items = {}
        for obj in self.client.list_objects(self.settings.bucket, prefix=self.settings.read_prefix + "/", recursive=True):
            if not obj.object_name.endswith("/metadata.json"):
                continue
            try:
                metadata = json.loads(self._read(obj.object_name))
                if metadata.get("templateKind", "part") != "component":
                    continue
                identifier = metadata["publicationId"]
                if not identifier or any(char in identifier for char in ("/", "\\")):
                    raise ValueError("invalid publication id")
                directory = obj.object_name.removesuffix("/metadata.json")
                if directory.rsplit("/", 1)[-1] != identifier or metadata.get("objectKey") != directory + "/template.rwpart":
                    raise ValueError("invalid object key")
                self._public(metadata)
                if identifier in items and items[identifier]["sha256"] != metadata["sha256"]:
                    raise ValueError("conflicting publication id")
                items[identifier] = metadata
            except (ValueError, KeyError, TypeError, ValidationError):
                raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR") from None
        return sorted(items.values(), key=lambda item: item["createdAt"], reverse=True)

    @storage_errors
    def list(self) -> dict:
        items = [self._public(item) for item in self._metadata()]
        return {"items": items, "count": len(items)}

    def _package(self, publication_id: str) -> tuple[bytes, dict]:
        metadata = next((item for item in self._metadata() if item["publicationId"] == publication_id), None)
        if metadata is None:
            raise ComponentError(404, "COMPONENT_PUBLICATION_NOT_FOUND")
        payload = self._read(metadata["objectKey"])
        if len(payload) != metadata["size"] or hashlib.sha256(payload).hexdigest().lower() != metadata["sha256"].lower():
            raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR")
        return payload, metadata

    @storage_errors
    def package(self, publication_id: str) -> bytes:
        return self._package(publication_id)[0]

    @storage_errors
    def detail(self, publication_id: str) -> dict:
        payload, metadata = self._package(publication_id)
        return self._parse_package(payload, metadata)

    @staticmethod
    def _parse_package(payload: bytes, metadata: dict) -> dict:
        try:
            with ZipFile(BytesIO(payload)) as archive:
                # Component documents are small. Bound decompression of untrusted archives.
                if any(archive.getinfo(name).file_size > 8 * 1024 * 1024 for name in ("manifest.json", "component.json", "checks.json")):
                    raise ValueError("oversized document")
                manifest = json.loads(archive.read("manifest.json"))
                document = ComponentDocument.model_validate_json(archive.read("component.json")).model_dump(by_alias=True)
                checks = json.loads(archive.read("checks.json"))
                if manifest.get("schemaVersion") != "component-package-1.0" or manifest.get("templateKind") != "component":
                    raise ValueError("unsupported package")
                if any(manifest.get(key) != metadata[key] for key in ("publicationId", "templateId", "version")):
                    raise ValueError("package identity mismatch")
                if not isinstance(checks, list):
                    raise ValueError("invalid checks")
        except (BadZipFile, KeyError, ValueError, TypeError, RuntimeError, ValidationError):
            raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR") from None
        return {**ComponentPublications._public(metadata), "document": document, "checks": checks}

    def _dependencies(self, document: dict):
        for member in document["assembly"]["members"]:
            reference = member["template"]
            if reference.get("templateKind", "part") != "component":
                continue
            dependency = self.detail(reference["publicationId"])
            if any(dependency[key] != reference[key] for key in ("templateId", "version")) or dependency["sha256"].lower() != reference["sha256"].lower():
                raise ComponentError(422, "COMPONENT_REFERENCE_INVALID")

    def _upload(self, snapshot: dict) -> dict:
        self._require_storage()
        identifier = snapshot["publicationId"]
        existing = next((item for item in self._metadata() if item["publicationId"] == identifier), None)
        if existing is not None:
            if existing["sha256"] != snapshot["sha256"]:
                raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR")
            return self.detail(identifier)
        self._dependencies(snapshot["document"])
        payload = self.drafts.publication_package(identifier)
        if hashlib.sha256(payload).hexdigest() != snapshot["sha256"]:
            raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR")
        try:
            self._parse_package(payload, snapshot)
        except ComponentError:
            raise ComponentError(422, "COMPONENT_PUBLICATION_INVALID") from None
        directory = f"{self.settings.prefix}/{identifier}"
        metadata = {key: value for key, value in snapshot.items() if key not in ("document", "checks")}
        metadata["objectKey"] = directory + "/template.rwpart"
        self._put(metadata["objectKey"], payload, "application/octet-stream")
        if self._read(metadata["objectKey"]) != payload:
            raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR")
        # Commit marker last: failed package uploads cannot appear in the library.
        self._put(directory + "/metadata.json", json.dumps(metadata, ensure_ascii=False).encode("utf-8"), "application/json")
        return self.detail(identifier)

    @storage_errors
    def publish(self, draft_id: str, expected_revision: int) -> dict:
        draft = self.drafts.get(draft_id)
        if draft["document"]["revision"] != expected_revision:
            raise ComponentError(409, "REVISION_CONFLICT")
        self._require_storage()
        self._dependencies(draft["document"])
        snapshot = self.drafts.prepare_publication(draft_id, expected_revision)
        return self._upload(snapshot)

    @storage_errors
    def migrate(self) -> dict:
        self._require_storage()
        # Local snapshots were inserted in dependency order. Preserve their bytes and IDs.
        identifiers = [item["publicationId"] for item in reversed(self.drafts.list_publications()["items"])]
        for identifier in identifiers:
            self._upload(self.drafts.publication(identifier))
        return {"count": len(identifiers)}
