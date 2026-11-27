from __future__ import annotations

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
from .hardware_selection import audit_hardware_selection
from pydantic import TypeAdapter


def envelope(draft_id: str, document: ComponentDocument, *, code_unique: bool = True) -> dict:
    data = document.model_dump(by_alias=True)
    return {"draftId": draft_id, "document": data, "checks": validate_component(document, code_unique=code_unique),
            "hardwareSelection": audit_hardware_selection(data)}


class DraftRepository:
    def __init__(self, path: str | Path):
        self.on_change = lambda draft_id: None
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
            connection.execute("""CREATE TABLE IF NOT EXISTS component_resize_history (
                draft_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, checksum TEXT NOT NULL,
                applied_revision INTEGER NOT NULL, before_document TEXT NOT NULL
            )""")
            connection.execute("CREATE TABLE IF NOT EXISTS component_qualification_configs (draft_id TEXT PRIMARY KEY, config TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS component_qualifications (draft_id TEXT PRIMARY KEY, report TEXT NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS component_resize_failures (
                draft_id TEXT NOT NULL, revision INTEGER NOT NULL, design_hash TEXT NOT NULL,
                target_hash TEXT NOT NULL, error TEXT NOT NULL, PRIMARY KEY(draft_id, revision, design_hash, target_hash))""")
            self._backfill_missing_codes(connection)

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
            connection.execute("BEGIN IMMEDIATE")
            if not document.definition.code.strip():
                data = document.model_dump(by_alias=True)
                data["definition"]["code"] = self._next_code(connection)
                document = ComponentDocument.model_validate(data)
            document = self._store_embedded_evidence(connection, draft_id, document)
            self._require_evidence_content(connection, draft_id, document)
            connection.execute("INSERT INTO component_drafts VALUES (?, ?, ?, ?, ?)", (
                draft_id, document.definition.name, document.revision, document.model_dump_json(by_alias=True), self._now(),
            ))
            self.on_change(draft_id)
            return envelope(draft_id, document, code_unique=self._code_is_unique(connection, document.definition.code, draft_id))

    def list(self) -> dict:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, name, revision, updated_at, document FROM component_drafts ORDER BY updated_at DESC, id").fetchall()
        return {"items": [{"draftId": row["id"], "name": row["name"], "code": json.loads(row["document"])["definition"]["code"],
                           "revision": row["revision"], "updatedAt": row["updated_at"]} for row in rows]}

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

    def cleanup_sources(self):
        """Read current drafts and immutable retry snapshots in one local snapshot."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            drafts = connection.execute("SELECT id, document FROM component_drafts").fetchall()
            publications = connection.execute("SELECT id, document FROM component_publications").fetchall()
        return [{"kind": kind, "id": row["id"],
                 "document": ComponentDocument.model_validate_json(row["document"]).model_dump(by_alias=True)}
                for kind, rows in (("local-draft", drafts), ("local-snapshot", publications)) for row in rows]

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
            hardware = audit_hardware_selection(document.model_dump(by_alias=True))
            if not hardware["complete"]:
                raise ComponentError(422, "HARDWARE_SELECTION_REQUIRED",
                                     message="已连接接口需要五金时必须完成五金配置；无需五金须明确说明。",
                                     details=[{"id": entry["id"], "message": entry["label"], "issues": entry["issues"]}
                                              for entry in hardware["entries"] if entry["status"] == "pending"])
            qualification = self._qualification(connection, draft_id, document)
            if not qualification["deliveryReady"]:
                raise ComponentError(409, "AUTHORING_QUALIFICATION_REQUIRED")
            existing = connection.execute("SELECT id FROM component_publications WHERE draft_id = ? AND revision = ?", (draft_id, expected_revision)).fetchone()
            if existing:
                row = self._publication(connection, existing["id"])
                return {**json.loads(row["metadata"]), "document": json.loads(row["document"]), "checks": json.loads(row["checks"])}
            code_unique = self._code_is_unique(connection, document.definition.code, draft_id)
            checks = validate_component(document, code_unique=code_unique)
            code_checks_pass = all(next(check for check in checks if check["id"] == identifier)["status"] == "pass"
                                   for identifier in ("code.format", "code.unique"))
            if (not code_checks_pass or not document.definition.name.strip()
                    or (document.assembly.members and next(check for check in checks if check["id"] == "references")["status"] != "pass")
                    or any(check["id"] in {"mates", "slots"} and check["status"] == "fail" for check in checks)
                    or (document.assembly.members and any(check["id"] == "references" and check["status"] == "fail" for check in checks))):
                raise ComponentError(422, "COMPONENT_PUBLICATION_INVALID")
            # Dependency checks use the shared publication service before preparing this
            # immutable retry snapshot. Network operations never hold the SQLite write lock.
            publication_id = f"component-{uuid4()}"
            version = connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM component_publications WHERE draft_id = ?", (draft_id,)).fetchone()[0]
            manifest = {"schemaVersion": "component-package-1.1" if document.geometryDesign else "component-package-1.0", "templateKind": "component", "publicationId": publication_id,
                        "templateId": draft_id, "version": version, "createdAt": self._now(), "sourceRevision": document.revision}
            data = document.model_dump(by_alias=True)
            output = BytesIO()
            with ZipFile(output, "w") as package:
                documents = [("manifest.json", manifest), ("component.json", data), ("checks.json", checks), ("qualification.json", qualification)]
                if document.definition.evidenceFiles:
                    documents.append(("evidence.json", self._portable_evidence(connection, draft_id, document)))
                for name, value in documents:
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

    def update(self, draft_id: str, expected_revision: int, action: TypedAction | list[TypedAction], *, actor: str = "user") -> dict:
        if actor not in ("user", "agent"):
            raise ValueError("Unknown edit source")
        expected_revision = TypeAdapter(Revision).validate_python(expected_revision)
        actions = [ComponentAction.validate_python(item) for item in (action if isinstance(action, list) else [action])]
        if not 1 <= len(actions) <= 100:
            raise ComponentError(422, "INVALID_ACTION_BATCH")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._read(connection, draft_id)
            reconciled = False
            if document.revision != expected_revision:
                history = connection.execute("""SELECT COUNT(*) AS total,
                    SUM(CASE WHEN actor = 'agent' THEN 1 ELSE 0 END) AS agents
                    FROM component_draft_changes WHERE draft_id = ? AND revision > ? AND revision <= ?""",
                    (draft_id, expected_revision, document.revision)).fetchone()
                reconciled = (actor == "user" and all(item.type != "reset" for item in actions) and expected_revision < document.revision
                              and history["total"] == history["agents"] == document.revision - expected_revision)
                if not reconciled:
                    raise ComponentError(409, "REVISION_CONFLICT", self._envelope(connection, draft_id, document, expected_revision))
            try:
                updated = document
                for item in actions:
                    updated = apply_action(updated, item)
                if updated.revision != document.revision:
                    updated = updated.model_copy(update={"revision": document.revision + 1})
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
                self.on_change(draft_id)
            result = self._envelope(connection, draft_id, updated, expected_revision)
            result["reconciled"] = reconciled
            return result

    def replace_geometry(self, draft_id: str, expected_revision: int, candidate: dict, *, history: dict | None = None, actor: str = "user") -> dict:
        """Apply only server-computed results, with strict compare-and-swap."""
        if actor not in ("user", "agent"):
            raise ValueError("Unknown edit source")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            original = self._read(connection, draft_id)
            if original.revision != expected_revision:
                raise ComponentError(409, "REVISION_CONFLICT")
            updated = ComponentDocument.model_validate({**candidate, "revision": expected_revision + 1})
            self._require_evidence_content(connection, draft_id, updated)
            now = self._now()
            connection.execute("UPDATE component_drafts SET name=?, revision=?, document=?, updated_at=? WHERE id=?", (
                updated.definition.name, updated.revision, updated.model_dump_json(by_alias=True), now, draft_id))
            changes = document_changes(original.model_dump(by_alias=True), updated.model_dump(by_alias=True))
            connection.execute("INSERT INTO component_draft_changes VALUES (?, ?, ?, ?, ?)", (draft_id, updated.revision, actor, now, json.dumps(changes, ensure_ascii=False)))
            if history:
                connection.execute("INSERT OR REPLACE INTO component_resize_history VALUES (?, ?, ?, ?, ?)",
                    (draft_id, history["jobId"], history["checksum"], updated.revision, original.model_dump_json(by_alias=True)))
            self.on_change(draft_id)
            return self._envelope(connection, draft_id, updated, expected_revision)

    def resize_history(self, draft_id):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM component_resize_history WHERE draft_id=?", (draft_id,)).fetchone()
            if row is None: return None
            return {"jobId": row["job_id"], "draftId": draft_id, "status": "applied", "appliedRevision": row["applied_revision"],
                "expectedRevision": row["applied_revision"]-1, "before": json.loads(row["before_document"]), "result": {"checksum": row["checksum"]}}

    @staticmethod
    def _qualification_config(connection, draft_id):
        from .qualification_policy import QualificationConfig
        row = connection.execute("SELECT config FROM component_qualification_configs WHERE draft_id=?", (draft_id,)).fetchone()
        return QualificationConfig.model_validate_json(row[0]).model_dump() if row else QualificationConfig().model_dump()

    @classmethod
    def _qualification(cls, connection, draft_id, document):
        from .qualification_policy import digest, QUALIFICATION_VERSION
        config = cls._qualification_config(connection, draft_id)
        row = connection.execute("SELECT report FROM component_qualifications WHERE draft_id=?", (draft_id,)).fetchone()
        report = json.loads(row[0]) if row else {"status": "pending", "message": "等待自动制作验收", "cases": []}
        current = (report.get("revision") == document.revision and report.get("inputHash") == digest(document.model_dump(by_alias=True))
                   and report.get("configHash") == digest(config) and report.get("policyVersion") == QUALIFICATION_VERSION)
        if row and not current:
            report = {"status": "stale", "message": "草稿或使用范围已变化，需重新验收", "cases": []}
        hardware = audit_hardware_selection(document.model_dump(by_alias=True))
        return {**report, "draftId": draft_id, "revision": document.revision, "config": config,
                "hardwareSelection": {key: hardware[key] for key in ("ruleVersion", "total", "configured", "notRequired", "pending", "complete")},
                "deliveryReady": current and report["status"] == "passed" and hardware["complete"]}

    def qualification(self, draft_id):
        with closing(self._connect()) as connection:
            return self._qualification(connection, draft_id, self._read(connection, draft_id))

    def configure_qualification(self, draft_id, expected_revision, config):
        from .qualification_policy import QualificationConfig
        config = QualificationConfig.model_validate(config).model_dump()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._read(connection, draft_id)
            if document.revision != expected_revision: raise ComponentError(409, "REVISION_CONFLICT")
            connection.execute("INSERT OR REPLACE INTO component_qualification_configs VALUES (?, ?)", (draft_id, json.dumps(config)))
            connection.execute("DELETE FROM component_qualifications WHERE draft_id=?", (draft_id,))
            self.on_change(draft_id)
        return self.qualification(draft_id)

    def save_qualification(self, draft_id, report, candidate=None):
        """Commit the verified nominal repair and its certificate in one CAS transaction."""
        from .qualification_policy import digest
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._read(connection, draft_id)
            if (document.revision != report["revision"] or digest(document.model_dump(by_alias=True)) != report["inputHash"]
                    or digest(self._qualification_config(connection, draft_id)) != report["configHash"]):
                raise ComponentError(409, "REVISION_CONFLICT")
            if candidate is not None:
                if report["status"] != "passed" or not report.get("cases") or any(c["status"] != "passed" for c in report["cases"]):
                    raise ComponentError(409, "AUTHORING_QUALIFICATION_REQUIRED")
                updated = ComponentDocument.model_validate({**candidate, "revision": document.revision + 1})
                if any(c["status"] == "fail" for c in validate_component(updated, code_unique=self._code_is_unique(connection, updated.definition.code, draft_id))):
                    raise ComponentError(422, "COMPONENT_PUBLICATION_INVALID")
                self._require_evidence_content(connection, draft_id, updated)
                now = self._now()
                connection.execute("UPDATE component_drafts SET name=?, revision=?, document=?, updated_at=? WHERE id=?",
                                   (updated.definition.name, updated.revision, updated.model_dump_json(by_alias=True), now, draft_id))
                changes = document_changes(document.model_dump(by_alias=True), updated.model_dump(by_alias=True))
                connection.execute("INSERT INTO component_draft_changes VALUES (?, ?, ?, ?, ?)",
                                   (draft_id, updated.revision, "agent", now, json.dumps(changes, ensure_ascii=False)))
                previous = connection.execute("SELECT applied_revision, before_document FROM component_resize_history WHERE draft_id=?", (draft_id,)).fetchone()
                # A background verification must not consume the user's undo of
                # the immediately preceding resize.
                before = previous["before_document"] if previous and previous["applied_revision"] == document.revision else document.model_dump_json(by_alias=True)
                connection.execute("INSERT OR REPLACE INTO component_resize_history VALUES (?, ?, ?, ?, ?)",
                                   (draft_id, report["jobId"], digest(candidate), updated.revision, before))
                report = {**report, "sourceRevision": document.revision, "revision": updated.revision,
                          "inputHash": digest(updated.model_dump(by_alias=True))}
                # No new change notification: this exact revision has just passed.
            connection.execute("INSERT OR REPLACE INTO component_qualifications VALUES (?, ?)", (draft_id, json.dumps(report, ensure_ascii=False)))
        return self.qualification(draft_id)

    def pending_qualifications(self):
        with closing(self._connect()) as connection:
            return [row[0] for row in connection.execute("SELECT draft_id, report FROM component_qualifications")
                    if json.loads(row[1]).get("status") in ("queued", "running")]

    def record_resize_failure(self, draft_id, revision, design_hash, dimensions, error):
        from .qualification_policy import digest
        if error.get("code") not in ("RESIZE_NO_SOLUTION", "RESIZE_INTERFACE_MISSING", "RESIZE_CONTACT_LOST"): return
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM component_resize_failures WHERE draft_id=? AND revision<>?", (draft_id, revision))
            connection.execute("INSERT OR REPLACE INTO component_resize_failures VALUES (?, ?, ?, ?, ?)",
                               (draft_id, revision, design_hash, digest(dimensions), json.dumps(error, ensure_ascii=False)))
            connection.execute("DELETE FROM component_resize_failures WHERE rowid IN (SELECT rowid FROM component_resize_failures WHERE draft_id=? ORDER BY rowid DESC LIMIT -1 OFFSET 32)", (draft_id,))

    def check_resize_target(self, draft_id, document, dimensions, design=None, retry=False):
        from .qualification_policy import digest, design_key
        with closing(self._connect()) as connection:
            config = self._qualification_config(connection, draft_id)
            if config["minimumDimensions"] is not None:
                for axis, (value, low, high) in enumerate(zip(dimensions, config["minimumDimensions"], config["maximumDimensions"])):
                    if value < low-.1 or value > high+.1:
                        return {"code": "RESIZE_OUTSIDE_CONTRACT", "message": f'{"XYZ"[axis]}={value:g} 超出声明的使用范围 {low:g}～{high:g} mm；请先修改使用范围并重新验收。', "details": []}
            if not retry:
                row = connection.execute("SELECT error FROM component_resize_failures WHERE draft_id=? AND revision=? AND design_hash=? AND target_hash=?",
                                         (draft_id, document["revision"], design_key(document, design), digest(dimensions))).fetchone()
                if row:
                    error = json.loads(row[0])
                    return {**error, "code": "RESIZE_KNOWN_FAILURE", "message": "相同尺寸和约束此前未通过试算："+error["message"]+"。可修改约束，或点击重新预览再次求解。"}
        return None

    def forget_resize_failure(self, draft_id, revision, design_hash, dimensions):
        from .qualification_policy import digest
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM component_resize_failures WHERE draft_id=? AND revision=? AND design_hash=? AND target_hash=?",
                               (draft_id, revision, design_hash, digest(dimensions)))

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
    def _portable_evidence(connection, draft_id, document):
        files = []
        for item in document.definition.evidenceFiles:
            row = connection.execute("SELECT content FROM component_draft_evidence WHERE draft_id=? AND id=?", (draft_id, item.id)).fetchone()
            if row is None:
                raise ComponentError(422, "EVIDENCE_CONTENT_MISSING")
            content = bytes(row[0])
            if len(content) != item.size or hashlib.sha256(content).hexdigest() != item.sha256:
                raise ComponentError(422, "INVALID_EVIDENCE")
            files.append({**item.model_dump(), "dataUrl": f"data:{item.mediaType};base64,{base64.b64encode(content).decode('ascii')}"})
        return files

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
        code_unique = DraftRepository._code_is_unique(connection, document.definition.code, draft_id)
        return {**envelope(draft_id, document, code_unique=code_unique), "changesTruncated": len(rows) > 50, "changes": [
            {"actor": row["actor"], "fromRevision": row["revision"] - 1, "revision": row["revision"],
             "createdAt": row["created_at"], "items": json.loads(row["items"])} for row in reversed(rows[:50])
        ]}

    @staticmethod
    def _code_is_unique(connection, code, exclude_id=None):
        normalized = code.strip().upper()
        if not normalized:
            return True
        rows = connection.execute("SELECT id, document FROM component_drafts").fetchall()
        return all(row["id"] == exclude_id or json.loads(row["document"])["definition"].get("code", "").strip().upper() != normalized for row in rows)

    @staticmethod
    def _next_code(connection):
        rows = connection.execute("SELECT document FROM component_drafts").fetchall()
        used = {json.loads(row["document"])["definition"].get("code", "").strip().upper() for row in rows}
        index = 1
        while f"CMP-{index:04d}" in used:
            index += 1
        return f"CMP-{index:04d}"

    @classmethod
    def _backfill_missing_codes(cls, connection):
        rows = connection.execute("SELECT id, document FROM component_drafts ORDER BY updated_at, id").fetchall()
        for row in rows:
            data = json.loads(row["document"])
            if data["definition"].get("code", "").strip():
                continue
            data["definition"]["code"] = cls._next_code(connection)
            connection.execute("UPDATE component_drafts SET document = ? WHERE id = ?",
                               (json.dumps(data, ensure_ascii=False, separators=(",", ":")), row["id"]))

    @staticmethod
    def _read(connection, draft_id: str) -> ComponentDocument:
        row = connection.execute("SELECT document FROM component_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ComponentError(404, "DRAFT_NOT_FOUND")
        return ComponentDocument.model_validate(json.loads(row["document"]))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
