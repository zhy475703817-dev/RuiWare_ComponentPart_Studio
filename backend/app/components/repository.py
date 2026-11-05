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
from .ports import basis_current, digest, port_diagnostics, source_context, trial_context, trial_ports
from pydantic import TypeAdapter


def envelope(draft_id: str, document: ComponentDocument, targets=None) -> dict:
    return {"draftId": draft_id, "document": document.model_dump(by_alias=True), "checks": validate_component(document), "portDiagnostics": port_diagnostics(document, targets)}


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

            connection.execute("""CREATE TABLE IF NOT EXISTS component_port_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL,
                revision INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
                action TEXT NOT NULL, result TEXT NOT NULL
            )""")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, name: str | None = None, document: ComponentDocument | None = None, *, actor: str = "user") -> dict:
        if document is None:
            document = create_component_document()
        else:
            document = ComponentDocument.model_validate(document.model_dump(by_alias=True))
        if actor == "agent" and any(port.basisConfirmation or port.reviewConfirmation for port in document.ports):
            raise ComponentError(403, "HUMAN_CONFIRMATION_REQUIRED", message="AI 导入不能携带人工确认，请由用户重新核对。")
        if name is not None:
            data = document.model_dump(by_alias=True)
            data["definition"]["name"] = name
            document = ComponentDocument.model_validate(data)
        draft_id = str(uuid4())
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO component_drafts VALUES (?, ?, ?, ?, ?)", (
                draft_id, document.definition.name, document.revision, document.model_dump_json(by_alias=True), self._now(),
            ))
        return self.get(draft_id)

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

    def cleanup_sources(self):
        """Read current drafts and immutable retry snapshots in one local snapshot."""
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN')
            drafts = connection.execute('SELECT id, document FROM component_drafts').fetchall()
            publications = connection.execute('SELECT id, document FROM component_publications').fetchall()
        return [{'kind': kind, 'id': row['id'],
                 'document': ComponentDocument.model_validate_json(row['document']).model_dump(by_alias=True)}
                for kind, rows in (('local-draft', drafts), ('local-snapshot', publications)) for row in rows]

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

    def update(self, draft_id: str, expected_revision: int, action: TypedAction, *, actor: str = "user", reason: str = "") -> dict:
        action = ComponentAction.validate_python(action)
        if not (action.type.startswith("port.") or action.type == "interface.set"):
            return self._update_port_aware(draft_id, expected_revision, action, actor=actor, reason=reason[:2000])
        try:
            return self._update_port_aware(draft_id, expected_revision, action, actor=actor, reason=reason[:2000])
        except ComponentError as error:
            with closing(self._connect()) as connection, connection:
                row = connection.execute("SELECT revision FROM component_drafts WHERE id = ?", (draft_id,)).fetchone()
                if row:
                    # Do not nest the current envelope and its previous operation log inside the log.
                    failure = {key: value for key, value in error.as_dict()["error"].items() if key != "current"}
                    self._port_event(connection, draft_id, row["revision"], actor, action, {"status": "error", "expectedRevision": expected_revision, "reason": reason[:2000], "error": failure})
            raise

    def preview_ports(self, draft_id, expected_revision, port_id, options):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            document = self._read(connection, draft_id)
            if document.revision != expected_revision:
                raise ComponentError(409, "REVISION_CONFLICT", self._envelope(connection, draft_id, document, expected_revision))
            port, target, target_port = self._trial_inputs(connection, draft_id, document, port_id, options)
            return {"draftId": draft_id, "baseRevision": document.revision, "targetDraftId": options.targetDraftId,
                    "targetRevision": target.revision, "portId": port.id, "targetPortId": target_port.id,
                    **trial_ports(document, port, target, target_port, options)}

    def _trial_inputs(self, connection, draft_id, document, port_id, options):
        if options.targetDraftId == draft_id:
            raise ComponentError(422, "SELF_TRIAL", message="对外试配请选择另一个组件草稿；同组件内部连接请在第二步操作。")
        port = next((item for item in document.ports if item.id == port_id), None)
        target = self._read(connection, options.targetDraftId)
        if target.revision != options.expectedTargetRevision:
            raise ComponentError(409, "TARGET_REVISION_CONFLICT", message="目标组件已更新，请重新读取目标后试配。", details={"expectedRevision": options.expectedTargetRevision, "currentRevision": target.revision, "targetDraftId": options.targetDraftId})
        target_port = next((item for item in target.ports if item.id == options.targetPortId), None)
        if not port or not target_port:
            raise ComponentError(422, "PORT_TARGET_MISSING", message="本接口或目标接口不存在，请重新读取。")
        return port, target, target_port

    @staticmethod
    def _targets(connection, document):
        targets = {}
        for identifier in {port.trial.options.targetDraftId for port in document.ports if port.trial}:
            try: targets[identifier] = DraftRepository._read(connection, identifier)
            except ComponentError: pass
        return targets

    def _update_port_aware(self, draft_id: str, expected_revision: int, action: TypedAction, *, actor: str = "user", reason: str = "") -> dict:
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
                reconciled = (actor == "user" and not (action.type == "reset" or action.type.startswith("port.") or action.type == "interface.set") and expected_revision < document.revision
                              and history["total"] == history["agents"] == document.revision - expected_revision)
                if not reconciled:
                    raise ComponentError(409, "REVISION_CONFLICT", self._envelope(connection, draft_id, document, expected_revision))
            try:
                if action.type in ("port.confirm", "port.trial.record"):
                    port = next((item for item in document.ports if item.id == action.id), None)
                    if port is None:
                        raise ComponentError(404, "COMPONENT_ITEM_NOT_FOUND", message="对外接口不存在。")
                    data = document.model_dump(by_alias=True)
                    updated_port = next(item for item in data["ports"] if item["id"] == port.id)
                    if action.type == "port.trial.record":
                        port, target, target_port = self._trial_inputs(connection, draft_id, document, port.id, action.options)
                        report = trial_ports(document, port, target, target_port, action.options)["report"]
                        updated_port["trial"] = {"options": action.options.model_dump(), "sourceKey": digest(trial_context(document, port)),
                            "targetKey": digest(trial_context(target, target_port)), "revision": document.revision,
                            "createdAt": self._now(), "actor": actor, "report": report}
                    else:
                        if actor != "user":
                            raise ComponentError(403, "HUMAN_CONFIRMATION_REQUIRED", message="AI 可以检查与试配；基准和最终人工确认必须由用户执行。")
                        if not action.note.strip():
                            raise ComponentError(422, "CONFIRMATION_NOTE_REQUIRED", message="请记录确认依据或检查说明。")
                        context = source_context(document, port)
                        if not context["interface"] or not context["interface"].get("frame"):
                            raise ComponentError(422, "CONNECTOR_FRAME_REQUIRED", message="接口缺少坐标，请先补齐基准。")
                        if action.scope == "review":
                            diagnostics = next(item for item in port_diagnostics(document, self._targets(connection, document)) if item["portId"] == port.id)
                            if not basis_current(document, port) or not diagnostics["trialCurrent"] or port.trial.report["rules"]["status"] != "pass" or port.trial.report["alignment"]["status"] != "pass":
                                raise ComponentError(422, "REVIEW_NOT_READY", message="请先确认双方基准、补齐规则并重新试配，规则与对齐检查通过后再人工确认。")
                            context = {"context": trial_context(document, port), "trial": port.trial.model_dump()}
                        updated_port["basisConfirmation" if action.scope == "basis" else "reviewConfirmation"] = {
                            "key": digest(context), "snapshot": context if action.scope == "basis" else {},
                            "revision": document.revision, "createdAt": self._now(), "actor": "user", "note": action.note.strip()}
                    data["revision"] = document.revision + 1
                    updated = ComponentDocument.model_validate(data)
                else:
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
            if action.type.startswith("port.") or action.type == "interface.set":
                self._port_event(connection, draft_id, updated.revision, actor, action, {"status": "ok", "expectedRevision": expected_revision, "beforeRevision": document.revision, "reason": reason, "changes": document_changes(document.model_dump(by_alias=True), updated.model_dump(by_alias=True))})
            result = self._envelope(connection, draft_id, updated, expected_revision)
            result["reconciled"] = reconciled
            return result

    def _port_event(self, connection, draft_id, revision, actor, action, result):
        connection.execute("INSERT INTO component_port_events (draft_id, revision, actor, created_at, action, result) VALUES (?, ?, ?, ?, ?, ?)",
                           (draft_id, revision, actor, self._now(), action.model_dump_json(by_alias=True), json.dumps(result, ensure_ascii=False)))
        connection.execute("DELETE FROM component_port_events WHERE draft_id = ? AND seq NOT IN (SELECT seq FROM component_port_events WHERE draft_id = ? ORDER BY seq DESC LIMIT 100)", (draft_id, draft_id))

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

        draft = self.get(draft_id)
        document = ComponentDocument.model_validate(draft["document"])
        result = serialize_component(document, draft["portDiagnostics"])
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
        events = connection.execute("SELECT * FROM component_port_events WHERE draft_id = ? ORDER BY seq DESC LIMIT 50", (draft_id,)).fetchall()
        return {**envelope(draft_id, document, DraftRepository._targets(connection, document)),
            "operations": [{"id": row["seq"], "revision": row["revision"], "actor": row["actor"], "createdAt": row["created_at"], "action": json.loads(row["action"]), **json.loads(row["result"])} for row in events], "changesTruncated": len(rows) > 50, "changes": [
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
