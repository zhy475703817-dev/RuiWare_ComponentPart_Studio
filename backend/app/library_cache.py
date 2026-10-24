"""Rebuildable SQLite cache. Remote publication and dependency checks bypass it."""
from contextlib import closing
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sqlite3
import time

from .config import PROJECT_ROOT, Settings
from .library import LibraryError, PublishedTemplate


def engine_version() -> str:
    digest = hashlib.sha256()
    paths = sorted((PROJECT_ROOT / 'backend' / 'engine').rglob('*.py'))
    paths += [Path(__file__).with_name('reconstruction.py'), Path(__file__).with_name('preview.py')]
    for path in paths:
        digest.update(path.read_bytes())
    for dependency in ('numpy', 'cadquery-ocp', 'pydantic'):
        try:
            digest.update(f'{dependency}={version(dependency)}'.encode())
        except PackageNotFoundError:
            digest.update(f'{dependency}=missing'.encode())
    return digest.hexdigest()


class LibraryCache:
    def __init__(self, path: Path, settings: Settings):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Credentials only enter a digest; changing access scope never reuses another cache.
        source = (settings.api_url, settings.api_key, settings.minio.endpoint, settings.minio.bucket,
                  settings.minio.read_prefix, settings.minio.secure, settings.minio.access_key, settings.minio.secret_key)
        self.namespace = hashlib.sha256(json.dumps(source).encode()).hexdigest()
        self.engine = engine_version()
        with closing(self._connect()) as db, db:
            # Only derived model data is discarded when upgrading the cache schema.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(library_models)')}
            if columns and 'id' not in columns:
                db.execute('DROP TABLE library_models')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS library_packages (
                    source TEXT, kind TEXT, id TEXT, hash TEXT, metadata TEXT, payload BLOB, accessed REAL,
                    PRIMARY KEY (source, kind, id, hash));
                CREATE TABLE IF NOT EXISTS library_models (
                    source TEXT, id TEXT, hash TEXT, engine TEXT, available INTEGER, model BLOB, model_hash TEXT,
                    PRIMARY KEY (source, id, hash, engine));
                CREATE TABLE IF NOT EXISTS library_catalogs (
                    source TEXT, kind TEXT, initialized INTEGER, active TEXT, pending TEXT, checked REAL,
                    PRIMARY KEY (source, kind));
            ''')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def save_package(self, publication: PublishedTemplate, payload: bytes):
        if len(payload) != publication.size or hashlib.sha256(payload).hexdigest() != publication.sha256.lower():
            raise LibraryError(502, 'PACKAGE_INTEGRITY_ERROR')
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO library_packages VALUES (?, ?, ?, ?, ?, ?, ?)', (
                self.namespace, publication.templateKind, publication.publicationId, publication.sha256.lower(),
                publication.model_dump_json(exclude_unset=True), payload, time.time()))

    def publication(self, identifier: str, expected_hash: str | None = None, kind='part') -> PublishedTemplate | None:
        with closing(self._connect()) as db:
            rows = db.execute('SELECT metadata, hash FROM library_packages WHERE source=? AND kind=? AND id=? ORDER BY accessed DESC',
                              (self.namespace, kind, identifier)).fetchall()
        if not rows:
            return None
        row = next((r for r in rows if expected_hash and r['hash'] == expected_hash.lower()), rows[0])
        if expected_hash and row['hash'] != expected_hash.lower():
            raise LibraryError(409, 'PUBLICATION_CHANGED')
        return PublishedTemplate.model_validate_json(row['metadata'])

    def update_metadata(self, publication: PublishedTemplate):
        with closing(self._connect()) as db, db:
            db.execute('UPDATE library_packages SET metadata=? WHERE source=? AND kind=? AND id=? AND hash=?', (
                publication.model_dump_json(exclude_unset=True), self.namespace, publication.templateKind,
                publication.publicationId, publication.sha256.lower()))

    def package(self, publication: PublishedTemplate) -> bytes | None:
        key = self.namespace, publication.templateKind, publication.publicationId, publication.sha256.lower()
        with closing(self._connect()) as db, db:
            row = db.execute('SELECT payload FROM library_packages WHERE source=? AND kind=? AND id=? AND hash=?', key).fetchone()
            if not row:
                return None
            payload = row['payload']
            if len(payload) != publication.size or hashlib.sha256(payload).hexdigest() != publication.sha256.lower():
                db.execute('DELETE FROM library_packages WHERE source=? AND kind=? AND id=? AND hash=?', key)
                return None
            db.execute('UPDATE library_packages SET accessed=? WHERE source=? AND kind=? AND id=? AND hash=?', (time.time(), *key))
            return payload

    def model(self, publication: PublishedTemplate) -> bytes | bool | None:
        with closing(self._connect()) as db, db:
            row = db.execute('SELECT * FROM library_models WHERE source=? AND id=? AND hash=? AND engine=?',
                             (self.namespace, publication.publicationId, publication.sha256.lower(), self.engine)).fetchone()
            if row is None:
                return None
            if not row['available']:
                return False
            if not row['model'] or hashlib.sha256(row['model']).hexdigest() != row['model_hash']:
                db.execute('DELETE FROM library_models WHERE source=? AND id=? AND hash=? AND engine=?',
                           (self.namespace, publication.publicationId, publication.sha256.lower(), self.engine))
                return None
            return row['model']

    def save_model(self, publication: PublishedTemplate, model: bytes | bool):
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO library_models VALUES (?, ?, ?, ?, ?, ?, ?)', (
                self.namespace, publication.publicationId, publication.sha256.lower(), self.engine, isinstance(model, bytes),
                model if isinstance(model, bytes) else None,
                hashlib.sha256(model).hexdigest() if isinstance(model, bytes) else None))

    def catalog(self, kind: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM library_catalogs WHERE source=? AND kind=?', (self.namespace, kind)).fetchone()
        return {'initialized': bool(row['initialized']), 'active': json.loads(row['active']),
                'pending': json.loads(row['pending']) if row['pending'] is not None else None, 'checked': row['checked']} if row else {
                    'initialized': False, 'active': [], 'pending': None, 'checked': None}

    def stage(self, kind: str, items: list[dict], *, complete=True):
        state = self.catalog(kind)
        if state['initialized'] and not complete:
            return
        # First sync is progressive; subsequent syncs leave the visible snapshot untouched.
        active = state['active'] if state['initialized'] else items
        pending = items if state['initialized'] and complete else state['pending']
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO library_catalogs VALUES (?, ?, ?, ?, ?, ?)', (
                self.namespace, kind, state['initialized'] or complete, json.dumps(active),
                json.dumps(pending) if pending is not None else None, time.time() if complete else state['checked']))

    def apply(self):
        with closing(self._connect()) as db, db:
            db.execute('UPDATE library_catalogs SET active=pending, pending=NULL WHERE source=? AND pending IS NOT NULL', (self.namespace,))

    def visible(self, items: list[dict]) -> list[dict]:
        # Models checked by an older engine must be rechecked before becoming visible.
        with closing(self._connect()) as db:
            available = {(row['id'], row['hash']) for row in db.execute(
                'SELECT id, hash FROM library_models WHERE source=? AND engine=? AND available=1', (self.namespace, self.engine))}
        return [item for item in items if item.get('templateKind') == 'component'
                or (item['publicationId'], item['sha256'].lower()) in available]

    def snapshot(self) -> dict:
        states = [self.catalog(kind) for kind in ('part', 'component')]
        items = self.visible([item for state in states for item in state['active']])
        changes = 0
        for state in states:
            if state['pending'] is None:
                continue
            before = {item['publicationId']: item for item in state['active']}
            after = {item['publicationId']: item for item in state['pending']}
            changes += sum(before.get(key) != after.get(key) for key in before.keys() | after.keys())
        items.sort(key=lambda item: item['createdAt'], reverse=True)
        return {'items': items, 'count': len(items), 'updateCount': changes,
                'initialized': all(state['initialized'] for state in states),
                'checkedAt': min((state['checked'] for state in states if state['checked'] is not None), default=None)}
