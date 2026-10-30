from contextlib import closing
import base64
from datetime import datetime, timezone
import hashlib
import json
from io import BytesIO
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .changes import document_changes
from .models import ComponentAction, ComponentDocument, Revision, TypedAction
from .service import ComponentError, apply_action, create_component_document, validate_component
from pydantic import TypeAdapter


def envelope(draft_id: str, document: ComponentDocument) -> dict:
    return {"draftId": draft_id, "document": document.model_dump(by_alias=True), "checks": validate_component(document)}


class DraftRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS component_drafts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                document TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS component_draft_changes (
                draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
                actor TEXT NOT NULL, created_at TEXT NOT NULL, items TEXT NOT NULL,
                PRIMARY KEY (draft_id, revision)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS component_draft_evidence (
                draft_id TEXT NOT NULL, id TEXT NOT NULL, file_name TEXT NOT NULL,
                media_type TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
                PRIMARY KEY (draft_id, id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS component_publications (
                id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
                version INTEGER NOT NULL, metadata TEXT NOT NULL, document TEXT NOT NULL,
                checks TEXT NOT NULL, package BLOB NOT NULL,
                UNIQUE(draft_id, revision), UNIQUE(draft_id, version)
            )""")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, name: str | None = None, document: ComponentDocument | None = None) -> dict:
        if document is None:
            document = create_component_document()
        else:
            document = ComponentDocument.model_validate(document.model_dump(by_alias=True))
        if name is not None:
            data = document.model_dump(by_alias=True)
            data["definition"]["name"] = name
            document = ComponentDocument.model_validate(data)
        draft_id = str(uuid4())
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO component_drafts VALUES (?, ?, ?, ?, ?)", (
                draft_id, document.definition.name, document.revision, document.model_dump_json(by_alias=True), self._now(),
            ))
        return envelope(draft_id, document)

    def list(self) -> dict:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, name, revision, updated_at FROM component_drafts ORDER BY updated_at DESC, id").fetchall()
        return {"items": [{"draftId": row["id"], "name": row["name"], "revision": row["revision"], "updatedAt": row["updated_at"]} for row in rows]}

    def get(self, draft_id: str, after_revision: int = 0) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            document = self._read(connection, draft_id)
            return self._envelope(connection, draft_id, document, after_revision)

    def list_publications(self) -> dict:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT metadata FROM component_publications ORDER BY rowid DESC").fetchall()
        items = [json.loads(row["metadata"]) for row in rows]
        return {"items": items, "count": len(items)}

    def publication(self, publication_id: str) -> dict:
        with closing(self._connect()) as connection:
            row = self._publication(connection, publication_id)
            return {**json.loads(row["metadata"]), "document": json.loads(row["document"]), "checks": json.loads(row["checks"])}

    def publication_package(self, publication_id: str) -> bytes:
        with closing(self._connect()) as connection:
            return self._publication(connection, publication_id)["package"]

    @staticmethod
    def _publication(connection, publication_id):
        row = connection.execute("SELECT * FROM component_publications WHERE id = ?", (publication_id,)).fetchone()
        if row is None:
            raise ComponentError(404, "COMPONENT_PUBLICATION_NOT_FOUND")
        return row

    def prepare_publication(self, draft_id: str, expected_revision: int) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._read(connection, draft_id)
            if document.revision != expected_revision:
                raise ComponentError(409, "REVISION_CONFLICT")
            existing = connection.execute("SELECT id FROM component_publications WHERE draft_id = ? AND revision = ?", (draft_id, expected_revision)).fetchone()
            if existing:
                row = self._publication(connection, existing["id"])
                return {**json.loads(row["metadata"]), "document": json.loads(row["document"]), "checks": json.loads(row["checks"])}
            checks = validate_component(document)
            if not document.definition.name.strip() or (document.assembly.members and next(check for check in checks if check["id"] == "references")["status"] != "pass"):
                raise ComponentError(422, "COMPONENT_PUBLICATION_INVALID")
            # Dependency checks use the shared publication service before preparing this
            # immutable retry snapshot. Network operations never hold the SQLite write lock.
            publication_id = f"component-{uuid4()}"
            version = connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM component_publications WHERE draft_id = ?", (draft_id,)).fetchone()[0]
            manifest = {"schemaVersion": "component-package-1.0", "templateKind": "component", "publicationId": publication_id,
                        "templateId": draft_id, "version": version, "createdAt": self._now(), "sourceRevision": document.revision}
            data = document.model_dump(by_alias=True)
            output = BytesIO()
            with ZipFile(output, "w") as package:
                for name, value in (("manifest.json", manifest), ("component.json", data), ("checks.json", checks)):
                    info = ZipInfo(name)
                    info.compress_type = ZIP_DEFLATED
                    package.writestr(info, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            payload = output.getvalue()
            metadata = {**manifest, "name": document.definition.name, "code": document.definition.code.strip() or draft_id[:8],
                        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
                        "downloadUrl": f"/api/published-components/{publication_id}/download", "memberCount": len(document.assembly.members),
                        "engineeringStatus": "incomplete" if any(check["status"] != "pass" for check in checks) else "ready"}
            connection.execute("INSERT INTO component_publications VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                publication_id, draft_id, document.revision, version, json.dumps(metadata, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), payload,
            ))
            return {**metadata, "document": data, "checks": checks}

    def update(self, draft_id: str, expected_revision: int, action: TypedAction, *, actor: str = "user") -> dict:
        if actor not in ("user", "agent"):
            raise ValueError("Unknown edit source")
        expected_revision = TypeAdapter(Revision).validate_python(expected_revision)
        action = ComponentAction.validate_python(action)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._read(connection, draft_id)
            reconciled = False
            if document.revision != expected_revision:
                history = connection.execute("""SELECT COUNT(*) AS total,
                    SUM(CASE WHEN actor = 'agent' THEN 1 ELSE 0 END) AS agents
                    FROM component_draft_changes WHERE draft_id = ? AND revision > ? AND revision <= ?""",
                    (draft_id, expected_revision, document.revision)).fetchone()
                reconciled = (actor == "user" and action.type != "reset" and expected_revision < document.revision
                              and history["total"] == history["agents"] == document.revision - expected_revision)
                if not reconciled:
                    raise ComponentError(409, "REVISION_CONFLICT", self._envelope(connection, draft_id, document, expected_revision))
            try:
                updated = apply_action(document, action)
            except ComponentError as error:
                if reconciled:
                    code = "EDIT_TARGET_REMOVED" if error.code in ("COMPONENT_ITEM_NOT_FOUND", "INVALID_REFERENCE") else "EDIT_STRUCTURE_CONFLICT"
                    raise ComponentError(409, code, self._envelope(connection, draft_id, document, expected_revision)) from error
                raise
            if updated.revision != document.revision:
                now = self._now()
                connection.execute("UPDATE component_drafts SET name = ?, revision = ?, document = ?, updated_at = ? WHERE id = ?", (
                    updated.definition.name, updated.revision, updated.model_dump_json(by_alias=True), now, draft_id,
                ))
                items = document_changes(document.model_dump(by_alias=True), updated.model_dump(by_alias=True))
                connection.execute("INSERT INTO component_draft_changes VALUES (?, ?, ?, ?, ?)", (
                    draft_id, updated.revision, actor, now, json.dumps(items, ensure_ascii=False),
                ))
                self._remove_unreferenced_evidence(connection, draft_id, updated)
            result = self._envelope(connection, draft_id, updated, expected_revision)
            result["reconciled"] = reconciled
            return result

    def save_evidence(self, draft_id: str, evidence_id: str, file_name: str, media_type: str, content: bytes) -> dict:
        if not re.fullmatch(r"[0-9a-f-]{36}", evidence_id) or len(file_name) > 255 or not file_name.strip():
            raise ComponentError(422, "INVALID_EVIDENCE")
        if len(content) > 5 * 1024 * 1024:
            raise ComponentError(413, "EVIDENCE_TOO_LARGE")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM component_drafts WHERE id = ?", (draft_id,)).fetchone() is None:
                raise ComponentError(404, "DRAFT_NOT_FOUND")
            prior = connection.execute("SELECT size FROM component_draft_evidence WHERE draft_id = ? AND id = ?", (draft_id, evidence_id)).fetchone()
            total = connection.execute("SELECT COALESCE(SUM(size), 0) AS size FROM component_draft_evidence WHERE draft_id = ?", (draft_id,)).fetchone()["size"]
            if total - (prior["size"] if prior else 0) + len(content) > 15 * 1024 * 1024:
                raise ComponentError(413, "EVIDENCE_TOTAL_TOO_LARGE")
            digest = hashlib.sha256(content).hexdigest()
            connection.execute("""INSERT INTO component_draft_evidence VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id, id) DO UPDATE SET file_name=excluded.file_name,
                media_type=excluded.media_type, size=excluded.size, sha256=excluded.sha256, content=excluded.content""",
                (draft_id, evidence_id, file_name, media_type[:255] or "application/octet-stream", len(content), digest, content))
        return {"id": evidence_id, "fileName": file_name, "mediaType": media_type[:255] or "application/octet-stream",
                "size": len(content), "sha256": digest, "dataUrl": None}

    def get_evidence(self, draft_id: str, evidence_id: str) -> dict:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM component_draft_evidence WHERE draft_id = ? AND id = ?", (draft_id, evidence_id)).fetchone()
        if row is None:
            raise ComponentError(404, "EVIDENCE_NOT_FOUND")
        return {"id": row["id"], "fileName": row["file_name"], "mediaType": row["media_type"], "size": row["size"],
                "sha256": row["sha256"], "content": bytes(row["content"])}

    def delete_evidence(self, draft_id: str, evidence_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM component_draft_evidence WHERE draft_id = ? AND id = ?", (draft_id, evidence_id))

    def export(self, draft_id: str) -> dict:
        from .service import serialize_component

        document = ComponentDocument.model_validate(self.get(draft_id)["document"])
        result = serialize_component(document)
        for evidence in result["stages"]["definition"].get("evidence", []):
            try:
                stored = self.get_evidence(draft_id, evidence["id"])
            except ComponentError:
                continue
            evidence["dataUrl"] = f"data:{stored['mediaType']};base64,{base64.b64encode(stored['content']).decode('ascii')}"
        return result

    @staticmethod
    def _remove_unreferenced_evidence(connection, draft_id: str, document: ComponentDocument):
        referenced = {item.id for item in document.definition.evidence}
        rows = connection.execute("SELECT id FROM component_draft_evidence WHERE draft_id = ?", (draft_id,)).fetchall()
        for row in rows:
            if row["id"] not in referenced:
                connection.execute("DELETE FROM component_draft_evidence WHERE draft_id = ? AND id = ?", (draft_id, row["id"]))

    @staticmethod
    def _envelope(connection, draft_id, document, after_revision):
        rows = connection.execute("""SELECT * FROM component_draft_changes
            WHERE draft_id = ? AND revision > ? AND revision <= ? ORDER BY revision DESC LIMIT 51""",
            (draft_id, after_revision, document.revision)).fetchall()
        return {**envelope(draft_id, document), "changesTruncated": len(rows) > 50, "changes": [
            {"actor": row["actor"], "fromRevision": row["revision"] - 1, "revision": row["revision"],
             "createdAt": row["created_at"], "items": json.loads(row["items"])} for row in reversed(rows[:50])
        ]}

    @staticmethod
    def _read(connection, draft_id: str) -> ComponentDocument:
        row = connection.execute("SELECT document FROM component_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ComponentError(404, "DRAFT_NOT_FOUND")
        return ComponentDocument.model_validate(json.loads(row["document"]))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
