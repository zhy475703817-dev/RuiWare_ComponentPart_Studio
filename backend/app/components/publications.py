"""Shared component releases. SQLite snapshots are retries, never the public library."""
from functools import wraps
import base64
import hashlib
from io import BytesIO
import json
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from ..config import MinioSettings
from ..library import LibraryError, PublishedTemplate
from ..published_storage import PublishedStorage
from .models import ComponentDocument, EvidenceFile
from .qualification_policy import digest
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
    def __init__(self, drafts: DraftRepository, settings: MinioSettings, storage: PublishedStorage | None = None):
        self.drafts = drafts
        self.settings = settings
        self.storage = storage or PublishedStorage(settings)

    def _require_storage(self):
        try:
            self.storage.require_write()
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None

    def _read(self, key: str) -> bytes:
        try:
            return self.storage.read(key)
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None

    def _put(self, key: str, payload: bytes, content_type: str):
        try:
            self.storage.put(key, payload, content_type)
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None

    @staticmethod
    def _public(metadata: dict) -> dict:
        return PublishedTemplate.model_validate(metadata).model_dump(exclude_unset=True)

    def _metadata(self) -> list[dict]:
        try:
            return [{**record.publication.model_dump(exclude_unset=True), "objectKey": record.object_key}
                    for record in self.storage.records("component")]
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None

    @storage_errors
    def list(self) -> dict:
        items = [self._public(item) for item in self._metadata()]
        return {"items": items, "count": len(items)}

    def _package(self, publication_id: str) -> tuple[bytes, dict]:
        try:
            record = next((item for item in self.storage.records("component")
                           if item.publication.publicationId == publication_id), None)
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None
        if record is None:
            raise ComponentError(404, "COMPONENT_PUBLICATION_NOT_FOUND")
        metadata = {**record.publication.model_dump(exclude_unset=True), "objectKey": record.object_key}
        try:
            return self.storage.package(record), metadata
        except LibraryError as error:
            raise ComponentError(error.status, error.code) from None

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
                qualification = None
                if "qualification.json" in archive.namelist():
                    if archive.getinfo("qualification.json").file_size > 8 * 1024 * 1024:
                        raise ValueError("oversized qualification")
                    qualification = json.loads(archive.read("qualification.json"))
                    if not isinstance(qualification, dict):
                        raise ValueError("invalid qualification")
                evidence = []
                if "evidence.json" in archive.namelist():
                    if archive.getinfo("evidence.json").file_size > 22 * 1024 * 1024:
                        raise ValueError("oversized evidence")
                    evidence = json.loads(archive.read("evidence.json"))
                    if not isinstance(evidence, list) or len(evidence) > 10:
                        raise ValueError("invalid evidence")
                    evidence = [EvidenceFile.model_validate(item).model_dump() for item in evidence]
                    if len({item['id'] for item in evidence}) != len(evidence):
                        raise ValueError("duplicate evidence")
                    for item in evidence:
                        prefix = f"data:{item['mediaType']};base64,"
                        if not isinstance(item['dataUrl'], str) or not item['dataUrl'].startswith(prefix):
                            raise ValueError("missing evidence content")
                        content = base64.b64decode(item['dataUrl'][len(prefix):], validate=True)
                        if len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['sha256']:
                            raise ValueError("evidence hash mismatch")
                    if sum(item['size'] for item in evidence) > 15 * 1024 * 1024:
                        raise ValueError("oversized evidence")
                    if [{**item, 'dataUrl': None} for item in evidence] != document['definition']['evidenceFiles']:
                        raise ValueError("evidence metadata mismatch")
                if manifest.get("schemaVersion") not in ("component-package-1.0", "component-package-1.1") or manifest.get("templateKind") != "component":
                    raise ValueError("unsupported package")
                if any(manifest.get(key) != metadata[key] for key in ("publicationId", "templateId", "version")):
                    raise ValueError("package identity mismatch")
                if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
                    raise ValueError("invalid checks")
                if qualification and not isinstance(qualification.get('cases', []), list):
                    raise ValueError("invalid qualification cases")
        except (BadZipFile, KeyError, ValueError, TypeError, RuntimeError, ValidationError):
            raise ComponentError(502, "REMOTE_STORAGE_INTEGRITY_ERROR") from None
        attachments_complete = len(evidence) == len(document['definition']['evidenceFiles'])
        qualified = bool(qualification and qualification.get('deliveryReady') is True
            and qualification.get('status') == 'passed' and qualification.get('revision') == document['revision']
            and qualification.get('inputHash') == digest(document) and qualification.get('configHash') == digest(qualification.get('config'))
            and qualification.get('cases') and all(isinstance(c, dict) and c.get('status') == 'passed' for c in qualification['cases'])
            and not any(check.get('status') == 'fail' for check in checks))
        return {**ComponentPublications._public(metadata), "document": document, "checks": checks,
                "qualification": qualification, "evidenceFiles": evidence,
                "readiness": {"templateStatus": "qualified" if qualified and attachments_complete else "unverified",
                              "attachmentsComplete": attachments_complete,
                              "engineeringStatus": "incomplete" if any(c['status'] != 'pass' for c in checks) else "ready",
                              "scope": qualification.get('scope', '') if qualification else ''}}

    def _dependencies(self, document: dict):
        for member in document["assembly"]["members"]:
            reference = member["template"]
            if reference.get("templateKind", "part") != "component":
                continue
            dependency = self.detail(reference["publicationId"])
            if any(dependency[key] != reference[key] for key in ("templateId", "version")) or dependency["sha256"].lower() != reference["sha256"].lower():
                raise ComponentError(422, "COMPONENT_REFERENCE_INVALID")
            if member.get("instanceDocument"):
                self._dependencies(member["instanceDocument"])

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
        if not self.drafts.qualification(draft_id)["deliveryReady"]:
            raise ComponentError(409, "AUTHORING_QUALIFICATION_REQUIRED")
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
