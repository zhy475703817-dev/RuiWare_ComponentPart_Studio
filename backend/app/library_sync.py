"""Local-first catalog; network and CAD work never block the catalog response."""
import asyncio
from contextlib import suppress
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import httpx

from .components.service import ComponentError
from .library import LibraryError, latest_catalog_items, read_published_library
from .library_cleanup import cleanup_preview, execute_cleanup
from .preview import DOWNLOAD_TIMEOUT_SECONDS, MAX_DOWNLOAD_BYTES, download, read_package
from . import reconstruction


class LibrarySync:
    def __init__(self, app):
        self.app = app
        self.cache = app.state.library_cache
        self.task = None
        self.started_at = None
        self.errors = []
        self.rerun = False
        self.cleanup = {'status': 'idle', 'deleted': [], 'failed': [], 'candidateCount': 0}

    def start(self, force=False):
        if self.task is not None and not self.task.done():
            if force:
                self.rerun = True
            return
        if not force and self.started_at is not None and time.monotonic() - self.started_at < 300:
            return
        self.started_at = time.monotonic()
        self.task = asyncio.create_task(self.run())

    def snapshot(self):
        return {**self.cache.snapshot(), 'syncing': self.task is not None and not self.task.done(), 'errors': self.errors,
                'cleanup': self.cleanup}

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def run(self):
        while True:
            self.rerun = False
            self.errors = []
            # Sources fail independently: a parts outage does not hide reusable components.
            await asyncio.gather(self.sync_parts(), self.sync_components())
            if not self.rerun:
                break

    async def sync_parts(self):
        state = self.app.state
        items = []
        failed = False
        try:
            use_minio = state.settings.minio.read_configured
            if use_minio:
                self.cleanup = {'status': 'analyzing', 'deleted': [], 'failed': [], 'candidateCount': 0}
                try:
                    report = await asyncio.to_thread(cleanup_preview, state.published_storage, state.drafts, state.publications,
                                                     state.reference_registry, self.cache)
                    report = await asyncio.to_thread(state.cleanup_store.save_scan, report)
                    self.cleanup['candidateCount'] = report['summary']['strictCandidateCount']
                    if not report['scanComplete']:
                        self.cleanup['status'] = 'blocked'
                    elif not state.published_storage.writable:
                        self.cleanup['status'] = 'permission-denied'
                    else:
                        self.cleanup['status'] = 'cleaning'
                        selected = [item['publicationId'] for item in report['items'] if item.get('status') == 'auto-clean']
                        if selected:
                            result = await asyncio.to_thread(execute_cleanup, state, report, selected, automatic=True)
                            self.cleanup['deleted'] = [item for item in result['items'] if item['status'] == 'deleted']
                            self.cleanup['failed'] = [item for item in result['items'] if item['status'] != 'deleted']
                        self.cleanup['status'] = 'completed' if not self.cleanup['failed'] else 'partial'
                except Exception as error:
                    # Cleanup is maintenance; its failure must not stop catalog updates.
                    self.cleanup.update(status='failed', errorCode=error.code if isinstance(error, (LibraryError, ComponentError)) else 'CLEANUP_FAILED')
                records = await asyncio.to_thread(state.published_storage.records, 'part', include_history=True)
            else:
                self.cleanup = {'status': 'disabled', 'deleted': [], 'failed': [], 'candidateCount': 0}
                library = await read_published_library(state.http, state.settings)
                records = [item for item in library.items if item.templateKind == 'part']
            # Choose the newest release before admission: an invalid latest model
            # must not expose an older release as though it were current.
            publications = [record.publication if use_minio else record for record in records]
            latest_ids = {item['publicationId'] for item in latest_catalog_items([
                publication.model_dump(exclude_unset=True) for publication in publications])}
            for record in records:
                publication = record.publication if use_minio else record
                if publication.publicationId not in latest_ids:
                    continue
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', publication.publicationId) or publication.publicationId in {'.', '..'}:
                    raise LibraryError(502, 'LIBRARY_INVALID_RESPONSE')
                try:
                    payload = self.cache.package(publication)
                    if payload is None:
                        if publication.size > MAX_DOWNLOAD_BYTES:
                            raise LibraryError(502, 'PACKAGE_TOO_LARGE')
                        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
                            if use_minio:
                                payload = await asyncio.to_thread(state.published_storage.package, record)
                            else:
                                payload = await download(
                                    state.http,
                                    state.settings,
                                    f'/api/public/v1/templates/{quote(publication.publicationId, safe="")}/download',
                                    MAX_DOWNLOAD_BYTES,
                                )
                        self.cache.save_package(publication, payload)
                    # Validate current metadata even when identical bytes already have a model.
                    # A bad identity must never overwrite a previous good model result.
                    try:
                        await asyncio.to_thread(read_package, payload, publication, require_model=False)
                    except LibraryError as error:
                        if reconstruction.confirmed_unavailable(error):
                            continue
                        raise
                    self.cache.update_metadata(publication)
                    model = self.cache.model(publication)
                    if model is None:
                        async with state.reconstruction_slots:
                            documents = await asyncio.to_thread(reconstruction._package_documents, payload, publication)
                            draft, material = await asyncio.to_thread(reconstruction._draft_and_material, documents)
                            model = await asyncio.to_thread(reconstruction._run_worker, draft, material, {})
                        self.cache.save_model(publication, model)
                except LibraryError as error:
                    if reconstruction.confirmed_unavailable(error):
                        self.cache.save_model(publication, False)
                        continue
                    self.errors.append(error.code)
                    failed = True
                    continue
                except (TimeoutError, httpx.TimeoutException):
                    self.errors.append('LIBRARY_TIMEOUT')
                    failed = True
                    continue
                except httpx.RequestError:
                    self.errors.append('LIBRARY_UNREACHABLE')
                    failed = True
                    continue
                if isinstance(model, bytes) and model:
                    items.append(publication.model_dump(exclude_unset=True))
                    self.cache.stage('part', items, complete=False)
            # A partial scan must not turn an unreachable package into a deletion.
            if not failed:
                self.cache.stage('part', items)
        except (LibraryError, ComponentError) as error:
            if self.cleanup['status'] in ('analyzing', 'cleaning'):
                self.cleanup['status'] = 'blocked'
            self.errors.append(error.code)
        except (TimeoutError, httpx.TimeoutException):
            self.errors.append('LIBRARY_TIMEOUT')
        except httpx.RequestError:
            self.errors.append('LIBRARY_UNREACHABLE')
        except Exception:
            self.errors.append('LIBRARY_UPSTREAM_ERROR')

    async def sync_components(self):
        state = self.app.state
        service = state.publications
        items = []
        failed = False
        try:
            records = await asyncio.to_thread(state.published_storage.records, 'component')
            for record in records:
                publication = record.publication
                item = publication.model_dump(exclude_unset=True)
                try:
                    payload = self.cache.package(publication)
                    missing = payload is None
                    if payload is None:
                        payload = await asyncio.to_thread(state.published_storage.package, record)
                    await asyncio.to_thread(service._parse_package, payload, item)
                    if missing:
                        self.cache.save_package(publication, payload)
                    else:
                        self.cache.update_metadata(publication)
                except (LibraryError, ComponentError) as error:
                    self.errors.append(error.code)
                    failed = True
                    continue
                items.append(item)
                self.cache.stage('component', items, complete=False)
            if not failed:
                self.cache.stage('component', items)
        except (LibraryError, ComponentError) as error:
            self.errors.append(error.code)
        except Exception:
            self.errors.append('REMOTE_STORAGE_UNAVAILABLE')


router = APIRouter(prefix='/api/template-library', tags=['发布库缓存'])


@router.get('')
async def cached_library(request: Request):
    sync = request.app.state.library_sync
    sync.start()
    return JSONResponse(sync.snapshot(), headers={'Cache-Control': 'no-store'})


@router.post('/sync')
async def check_library(request: Request):
    sync = request.app.state.library_sync
    sync.start(force=True)
    return JSONResponse(sync.snapshot(), headers={'Cache-Control': 'no-store'})


@router.post('/apply')
async def apply_library(request: Request):
    request.app.state.library_cache.apply()
    return JSONResponse(request.app.state.library_sync.snapshot(), headers={'Cache-Control': 'no-store'})
