"""Shared draft references; failed/offline clients never expire automatically."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import platform
import sqlite3
from threading import Lock
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .library import LibraryError


logger = logging.getLogger(__name__)
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class RegistryReference(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    templateKind: Literal['part', 'component'] = 'part'
    publicationId: Identifier
    templateId: Identifier
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-fA-F0-9]{64}$')


class RegistrySource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['shared-draft', 'shared-snapshot']
    id: Identifier
    name: str = Field(max_length=256)
    references: list[RegistryReference] = Field(max_length=100000)


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schemaVersion: Literal['component-references-1.0']
    clientId: str = Field(pattern=r'^[0-9a-f-]{36}$')
    updatedAt: str
    sources: list[RegistrySource] = Field(max_length=100000)

    @field_validator('updatedAt')
    @classmethod
    def validate_date(cls, value):
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('timezone required')
        return value


class ReferenceRegistry:
    def __init__(self, drafts, storage):
        self.drafts, self.storage = drafts, storage
        settings = storage.settings
        self.source = hashlib.sha256(json.dumps((settings.endpoint, settings.bucket, settings.read_prefix,
                                                settings.prefix, settings.secure)).encode()).hexdigest()
        # A copied DB on another host or in another project must not overwrite its source.
        self.device = hashlib.sha256((platform.node() + str(drafts.path.resolve())).encode()).hexdigest()
        self.lock = Lock()
        self.event = asyncio.Event()
        self.stopping = False
        self.task = None
        with closing(self._connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS reference_clients (
                source TEXT, device TEXT, client_id TEXT, synced_digest TEXT, synced_at TEXT, error TEXT,
                PRIMARY KEY (source, device))''')
            db.execute('INSERT OR IGNORE INTO reference_clients VALUES (?, ?, ?, NULL, NULL, NULL)',
                       (self.source, self.device, str(uuid4())))
        self.client_id = self._state()['client_id']

    def _connect(self):
        db = sqlite3.connect(self.drafts.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def _state(self):
        with closing(self._connect()) as db:
            return dict(db.execute('SELECT * FROM reference_clients WHERE source=? AND device=?',
                                   (self.source, self.device)).fetchone())

    def _sources(self):
        sources = []
        for source in self.drafts.cleanup_sources():
            refs = {json.dumps({'templateKind': 'part', **member['template']}, sort_keys=True)
                    for member in source['document']['assembly']['members']}
            sources.append({'kind': 'shared-draft' if source['kind'] == 'local-draft' else 'shared-snapshot',
                            'id': source['id'], 'name': source['document']['definition']['name'][:256],
                            'references': [json.loads(ref) for ref in sorted(refs)]})
        sources.sort(key=lambda item: (item['kind'], item['id']))
        digest = hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return sources, digest

    def status(self):
        state = self._state()
        status = 'disabled'
        error = state['error']
        if self.storage.writable:
            try:
                _, digest = self._sources()
                status = 'error' if error else 'synchronized' if digest == state['synced_digest'] else 'pending'
            except Exception:
                status, error = 'error', 'LOCAL_REFERENCE_READ_FAILED'
        return {'status': status, 'clientId': self.client_id, 'lastSyncedAt': state['synced_at'],
                'error': error, 'deletionEnabled': False}

    def sync(self, force=False):
        if not self.storage.writable:
            return self.status()
        with self.lock:
            try:
                sources, digest = self._sources()
                state = self._state()
                now = datetime.now(timezone.utc)
                if (not force and not state['error'] and digest == state['synced_digest'] and state['synced_at']
                        and now - datetime.fromisoformat(state['synced_at']) < timedelta(minutes=5)):
                    return self.status()
                document = RegistryDocument.model_validate({'schemaVersion': 'component-references-1.0',
                    'clientId': self.client_id, 'updatedAt': now.isoformat(), 'sources': sources})
                payload = document.model_dump_json().encode()
                if len(payload) > 8 * 1024 * 1024:
                    raise LibraryError(422, 'REFERENCE_REGISTRY_TOO_LARGE')
                key = f'{self.storage.settings.prefix}/reference-registry/{self.client_id}.json'
                self.storage.put(key, payload, 'application/json')
                if self.storage.read(key) != payload:
                    raise LibraryError(502, 'REFERENCE_REGISTRY_INTEGRITY_ERROR')
                with closing(self._connect()) as db, db:
                    db.execute('UPDATE reference_clients SET synced_digest=?, synced_at=?, error=NULL WHERE source=? AND device=?',
                               (digest, now.isoformat(), self.source, self.device))
            except Exception as error:
                code = error.code if isinstance(error, LibraryError) else 'REFERENCE_SYNC_FAILED'
                with closing(self._connect()) as db, db:
                    db.execute('UPDATE reference_clients SET error=? WHERE source=? AND device=?',
                               (code, self.source, self.device))
        return self.status()

    def read_shared(self):
        self.storage.require_read()
        documents, issues = [], []
        try:
            objects = self.storage.client.list_objects(self.storage.settings.bucket,
                prefix=self.storage.settings.read_prefix + '/', recursive=True)
            for obj in objects:
                if '/reference-registry/' not in obj.object_name or not obj.object_name.endswith('.json'):
                    continue
                try:
                    payload = self.storage.read(obj.object_name)
                    if len(payload) > 8 * 1024 * 1024:
                        raise ValueError('oversized registry')
                    document = RegistryDocument.model_validate_json(payload)
                    if not obj.object_name.endswith(f'/reference-registry/{document.clientId}.json'):
                        raise ValueError('registry identity mismatch')
                    documents.append(document.model_dump())
                except Exception:
                    issues.append({'code': 'SHARED_REFERENCE_READ_FAILED'})
        except Exception:
            issues.append({'code': 'SHARED_REFERENCE_LIST_FAILED'})
        return documents, issues

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while not self.stopping:
            try:
                await asyncio.to_thread(self.sync)
            except Exception:
                # Keep retrying if the local DB temporarily cannot record sync status.
                logger.warning('Reference registration unavailable; retrying')
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.event.wait(), timeout=5)
                except TimeoutError:
                    pass

    async def close(self):
        self.stopping = True
        self.event.set()
        if self.task:
            await self.task


router = APIRouter(prefix='/api/template-library/references', tags=['共享引用登记'])


@router.get('')
async def reference_status(request: Request):
    status = await asyncio.to_thread(request.app.state.reference_registry.status)
    return JSONResponse(status, headers={'Cache-Control': 'no-store'})


@router.post('/sync')
async def sync_references(request: Request):
    registry = request.app.state.reference_registry
    return JSONResponse(await asyncio.to_thread(registry.sync, force=True), headers={'Cache-Control': 'no-store'})
