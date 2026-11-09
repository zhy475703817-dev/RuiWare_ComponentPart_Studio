"""Small local manifest/backup store for destructive publication cleanup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(path)


class CleanupStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.scans = self.root / "scans"
        self.backups = self.root / "backups"
        self.audit = self.root / "audit.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)

    def save_scan(self, report: dict) -> dict:
        now = datetime.now(timezone.utc)
        scan = {**report, "scanId": uuid4().hex, "savedAt": now.isoformat(),
                "expiresAt": (now + timedelta(hours=24)).isoformat()}
        _atomic_bytes(self.scans / f"{scan['scanId']}.json", json.dumps(scan, ensure_ascii=False).encode())
        return scan

    def load_scan(self, scan_id: str) -> dict | None:
        if not re.fullmatch(r"[a-f0-9]{32}", scan_id or ""):
            return None
        path = self.scans / f"{scan_id}.json"
        try:
            scan = json.loads(path.read_text(encoding="utf-8"))
            if datetime.fromisoformat(scan["expiresAt"]) < datetime.now(timezone.utc):
                return None
            return scan
        except (OSError, ValueError, KeyError):
            return None

    def write_backup(self, publication: dict, package: bytes, metadata: bytes) -> dict:
        backup_id = uuid4().hex
        package_hash = hashlib.sha256(package).hexdigest()
        metadata_hash = hashlib.sha256(metadata).hexdigest()
        directory = self.backups / backup_id
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {
            "backupId": backup_id,
            "publicationId": publication["publicationId"],
            "templateKind": publication.get("templateKind", "part"),
            "objectKey": publication.get("objectKey"),
            "sha256": package_hash,
            "metadataSha256": metadata_hash,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "expiresAt": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }
        try:
            _atomic_bytes(directory / "template.rwpart", package)
            _atomic_bytes(directory / "metadata.json", metadata)
            if hashlib.sha256((directory / "template.rwpart").read_bytes()).hexdigest() != package_hash:
                raise OSError("backup hash mismatch")
            if hashlib.sha256((directory / "metadata.json").read_bytes()).hexdigest() != metadata_hash:
                raise OSError("backup metadata hash mismatch")
            # Commit marker last: incomplete backups stay invisible. Avoid renaming
            # a populated directory, which Windows can deny even for writable files.
            _atomic_bytes(directory / "manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return manifest

    def list_backups(self) -> list[dict]:
        result = []
        for path in self.backups.glob("*/manifest.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda item: item.get("createdAt", ""), reverse=True)

    def backup(self, backup_id: str) -> tuple[dict, bytes, bytes] | None:
        if not re.fullmatch(r"[a-f0-9]{32}", backup_id or ""):
            return None
        directory = self.backups / backup_id
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            package = (directory / "template.rwpart").read_bytes()
            metadata = (directory / "metadata.json").read_bytes()
            if (hashlib.sha256(package).hexdigest() != manifest["sha256"] or
                    hashlib.sha256(metadata).hexdigest() != manifest["metadataSha256"]):
                return None
            return manifest, package, metadata
        except (OSError, ValueError, KeyError):
            return None

    def delete_backup(self, backup_id: str) -> bool:
        backup = self.backup(backup_id)
        if backup is None:
            return False
        shutil.rmtree(self.backups / backup_id)
        return True

    def append_audit(self, entry: dict) -> None:
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        with self.audit.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_audit(self) -> list[dict]:
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text(encoding='utf-8').splitlines() if line.strip()]
