from contextlib import closing
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

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
            document = self._store_embedded_evidence(connection, draft_id, document)
            self._require_evidence_content(connection, draft_id, document)
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
                updated = self._store_embedded_evidence(connection, draft_id, updated)
                self._require_evidence_content(connection, draft_id, updated)
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
        for evidence in result["stages"]["definition"]["evidenceFiles"]:
            stored = self.get_evidence(draft_id, evidence["id"])
            evidence["dataUrl"] = f"data:{stored['mediaType']};base64,{base64.b64encode(stored['content']).decode('ascii')}"
        return result

    @staticmethod
    def _store_embedded_evidence(connection, draft_id: str, document: ComponentDocument) -> ComponentDocument:
        data = document.model_dump(by_alias=True)
        files = data["definition"]["evidenceFiles"]
        for item in files:
            data_url = item.get("dataUrl")
            if not data_url:
                continue
            match = re.fullmatch(r"data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})", data_url)
            if not match or match.group(1) != item["mediaType"]:
                raise ComponentError(422, "INVALID_EVIDENCE")
            try:
                content = base64.b64decode(match.group(2), validate=True)
            except ValueError as error:
                raise ComponentError(422, "INVALID_EVIDENCE") from error
            if (len(content) != item["size"] or len(content) > 5 * 1024 * 1024
                    or hashlib.sha256(content).hexdigest() != item["sha256"]):
                raise ComponentError(422, "INVALID_EVIDENCE")
            connection.execute("""INSERT INTO component_draft_evidence VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id, id) DO UPDATE SET file_name=excluded.file_name,
                media_type=excluded.media_type, size=excluded.size, sha256=excluded.sha256, content=excluded.content""",
                (draft_id, item["id"], item["fileName"], item["mediaType"], len(content), item["sha256"], content))
            item["dataUrl"] = None
        clean = ComponentDocument.model_validate(data)
        return clean

    @staticmethod
    def _require_evidence_content(connection, draft_id: str, document: ComponentDocument):
        for item in document.definition.evidenceFiles:
            if connection.execute("SELECT 1 FROM component_draft_evidence WHERE draft_id = ? AND id = ?", (draft_id, item.id)).fetchone() is None:
                raise ComponentError(422, "EVIDENCE_CONTENT_MISSING")

    @staticmethod
    def _remove_unreferenced_evidence(connection, draft_id: str, document: ComponentDocument):
        referenced = {item.id for item in document.definition.evidenceFiles}
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
