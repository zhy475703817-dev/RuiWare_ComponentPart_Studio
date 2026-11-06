"""Scan, confirm and audit publication cleanup with local recovery backups."""
import asyncio
from datetime import datetime, timezone
from itertools import combinations
from pathlib import PurePosixPath
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .components.service import ComponentError
from .library import LibraryError, latest_catalog_items
from .package_similarity import analyze_package, compare_analyses
from .preview import read_package


def write_backup(store, publication: dict, package: bytes, metadata: bytes) -> dict:
    """Patchable boundary used by cleanup tests; the store verifies both hashes."""
    return store.write_backup(publication, package, metadata)


def execute_cleanup(state, report: dict, identifiers: list[str], *, automatic=False) -> dict:
    """One deletion path for sync and explicitly confirmed similarity candidates."""
    with state.cleanup_lock:
        if not report.get('scanComplete'):
            raise LibraryError(409, 'CLEANUP_SCAN_INCOMPLETE')
        saved = {item['publicationId']: item for item in report['items']}
        fresh = cleanup_preview(state.published_storage, state.drafts, state.publications,
                                state.reference_registry, state.library_cache)
        if not fresh['scanComplete']:
            raise LibraryError(409, 'CLEANUP_SCAN_INCOMPLETE')
        current = {item['publicationId']: item for item in fresh['items']}
        selected = set(identifiers)
        results = []
        deleted_ids = set()
        ordered = sorted(dict.fromkeys(identifiers),
                         key=lambda identifier: saved.get(identifier, {}).get('candidateClass') != 'old-release')
        for identifier in ordered:
            old, row = saved.get(identifier), current.get(identifier)
            if (not old or not row or old['sha256'] != row['sha256'] or
                    old['candidateClass'] != row['candidateClass'] or old['templateId'] != row['templateId'] or
                    old['version'] != row['version'] or old.get('objectKey') != row.get('objectKey')):
                results.append({'publicationId': identifier, 'status': 'rejected', 'errorCode': 'PUBLICATION_CHANGED'})
                continue
            category = row['candidateClass']
            allowed = ('old-release', 'exact-duplicate') if automatic else ('old-release', 'exact-duplicate', 'high-similarity')
            if row.get('templateKind', 'part') != 'part' or category not in allowed or old['candidateClass'] not in allowed:
                results.append({'publicationId': identifier, 'status': 'rejected', 'errorCode': 'CLEANUP_CANDIDATE_NOT_ALLOWED'})
                continue
            if row['status'] == 'blocked':
                results.append({'publicationId': identifier, 'status': 'rejected', 'errorCode': 'CLEANUP_SCAN_INCOMPLETE'})
                continue
            if category == 'high-similarity' and not any(
                    match['candidateClass'] == 'high-similarity' and match['publicationId'] not in selected
                    for match in row['matches']):
                results.append({'publicationId': identifier, 'status': 'rejected', 'errorCode': 'CLEANUP_KEEP_ONE_REQUIRED'})
                continue
            audit = {'publicationId': identifier, 'candidateClass': category, 'sha256': row['sha256'],
                     'references': row['references'], 'scores': row['scores'], 'automatic': automatic,
                     'templateId': row['templateId'], 'version': row['version'], 'matches': row['matches'],
                     'executor': 'component-platform',
                     'bucket': state.published_storage.settings.bucket, 'startedAt': datetime.now(timezone.utc).isoformat()}
            backup = None
            deleted = False
            deletion_started = False
            backup_ready = False
            payload = metadata = None
            try:
                record = state.published_storage.record('part', identifier, row['sha256'])
                if record.object_key != row['objectKey']:
                    raise LibraryError(409, 'PUBLICATION_CHANGED')
                if category != 'old-release' and any(
                        item.get('templateKind', 'part') == 'part' and item['templateId'] == row['templateId']
                        and item['publicationId'] != identifier and item['publicationId'] not in deleted_ids
                        for item in fresh['items']):
                    raise LibraryError(409, 'CLEANUP_HISTORY_REMAINS')
                if category == 'old-release':
                    same_template = [item for item in fresh['items'] if item.get('templateKind', 'part') == 'part' and item['templateId'] == row['templateId']]
                    survivors = latest_catalog_items(same_template)
                else:
                    survivors = [current[match['publicationId']] for match in row['matches']
                                 if match['publicationId'] not in selected and match['publicationId'] in current]
                if not survivors:
                    raise LibraryError(409, 'CLEANUP_KEEP_ONE_REQUIRED')
                for survivor in survivors:
                    keeper = state.published_storage.record('part', survivor['publicationId'], survivor['sha256'])
                    keeper_payload = state.published_storage.package(keeper)
                    read_package(keeper_payload, keeper.publication, require_model=False)
                    if analyze_package(keeper_payload)['candidateClass'] != 'analyzed':
                        raise LibraryError(409, 'CLEANUP_KEEPER_INVALID')
                payload = state.published_storage.package(record)
                metadata_key = record.object_key.rsplit('/', 1)[0] + '/metadata.json'
                metadata = state.published_storage.read(metadata_key)
                backup_ready = True
                backup = write_backup(state.cleanup_store, {**row, 'objectKey': record.object_key}, payload, metadata)
                audit.update(backupId=backup['backupId'], objectKey=record.object_key)
                state.cleanup_store.append_audit({**audit, 'status': 'deleting'})
                deletion_started = True
                deleted_versions = state.published_storage.delete_part(record, expected_sha256=row['sha256'])
                deleted = True
                deleted_ids.add(identifier)
                if not automatic:
                    state.library_cache.remove_publications([identifier])
                state.cleanup_store.append_audit({**audit, 'status': 'deleted', 'deletedVersions': deleted_versions,
                                                  'finishedAt': datetime.now(timezone.utc).isoformat()})
                results.append({'publicationId': identifier, 'status': 'deleted', 'backupId': backup['backupId'],
                                'deletedVersions': deleted_versions})
            except (LibraryError, OSError, sqlite3.Error) as error:
                if deleted:
                    result = {'publicationId': identifier, 'status': 'deleted', 'warningCode': 'CLEANUP_LOCAL_UPDATE_FAILED',
                              'deletedVersions': deleted_versions}
                else:
                    result = {'publicationId': identifier,
                              'status': 'failed' if backup or not backup_ready else 'backup-failed',
                              'errorCode': getattr(error, 'code', 'BACKUP_FAILED')}
                    if deletion_started and backup and payload is not None and metadata is not None:
                        try:
                            state.published_storage.recover_part_after_partial_delete(
                                record, package=payload, metadata=metadata)
                            result['recoveryStatus'] = 'restored-for-retry'
                        except LibraryError as recovery_error:
                            result['status'] = 'partial'
                            result['recoveryCode'] = recovery_error.code
                if backup:
                    result['backupId'] = backup['backupId']
                results.append(result)
                try:
                    state.cleanup_store.append_audit({**audit, **result, 'finishedAt': datetime.now(timezone.utc).isoformat()})
                except OSError:
                    pass
        return {'scanId': report.get('scanId'), 'items': results}


def cleanup_preview(storage, drafts, publications, registry=None, cache=None) -> dict:
    now = datetime.now(timezone.utc)
    records = storage.records(include_history=True)
    items = [{**record.publication.model_dump(exclude_unset=True), 'objectKey': record.object_key} for record in records]
    by_id = {(item.get('templateKind', 'part'), item['publicationId']): item for item in items}
    parts = [item for item in items if item.get('templateKind', 'part') == 'part']
    latest = {item['publicationId'] for item in latest_catalog_items(parts)}
    references = {key: [] for key in by_id}
    issues = []

    def collect_refs(refs, source):
        for ref in refs:
            key = ref.get('templateKind', 'part'), ref['publicationId']
            target = by_id.get(key)
            if target is None:
                issues.append({'code': 'REFERENCE_MISSING', 'sourceId': source['id'],
                               'severity': 'warning' if key[0] == 'part' else 'error'})
                continue
            if source not in references[key]:
                references[key].append(source)
            if (target['templateId'] != ref['templateId'] or target['version'] != ref['version']
                    or target['sha256'].lower() != ref['sha256'].lower()):
                issues.append({'code': 'REFERENCE_MISMATCH', 'sourceId': source['id']})

    def collect(document, source):
        collect_refs([member['template'] for member in document['assembly']['members']], source)

    # All published component versions remain supported, including the nested
    # components they reference. Scanning every version covers the entire graph.
    for record in records:
        item = record.publication
        if item.templateKind != 'component':
            continue
        try:
            payload = cache.package(item) if cache else None
            if payload is None:
                payload = storage.package(record)
                if cache:
                    cache.save_package(item, payload)
            detail = publications._parse_package(payload, item.model_dump(exclude_unset=True))
            collect(detail['document'], {'kind': 'published-component', 'id': item.publicationId, 'name': item.name})
        except (LibraryError, ComponentError) as error:
            issues.append({'code': error.code, 'sourceId': item.publicationId})
        except Exception:
            issues.append({'code': 'COMPONENT_SCAN_FAILED', 'sourceId': item.publicationId})

    try:
        for source in drafts.cleanup_sources():
            collect(source['document'], {key: source[key] for key in ('kind', 'id')} |
                    {'name': source['document']['definition']['name']})
    except Exception:
        issues.append({'code': 'LOCAL_SCAN_FAILED'})

    shared = []
    if registry is not None:
        shared, shared_issues = registry.read_shared()
        issues.extend(shared_issues)
        for client in shared:
            for source in client['sources']:
                collect_refs(source['references'], {key: source[key] for key in ('kind', 'id', 'name')} |
                             {'clientId': client['clientId']})
    # A reported stale client is informational only; its registered references remain in the scan.
    stale = sum((now - datetime.fromisoformat(client['updatedAt'].replace('Z', '+00:00'))).total_seconds() > 86400
                for client in shared)

    payloads = {}
    analyses = {}
    for record in records:
        publication = record.publication
        if publication.templateKind != 'part':
            continue
        try:
            payload = cache.package(publication) if cache else None
            if payload is None:
                payload = storage.package(record)
                if cache:
                    cache.save_package(publication, payload)
            payloads[publication.publicationId] = payload
            analyses[publication.publicationId] = analyze_package(payload)
        except LibraryError as error:
            issues.append({'code': error.code, 'sourceId': publication.publicationId})

    reasons = {item['publicationId']: set() for item in parts}
    candidate_class = {item['publicationId']: 'none' for item in parts}
    matches = {item['publicationId']: [] for item in parts}
    for item in parts:
        if item['publicationId'] not in latest:
            reasons[item['publicationId']].add('old-release')
            candidate_class[item['publicationId']] = 'old-release'

    by_hash = {}
    for item in parts:
        if item['publicationId'] not in latest:
            continue
        by_hash.setdefault(item['sha256'].lower(), []).append(item)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        keeper = max(group, key=lambda item: (datetime.fromisoformat(item['createdAt'].replace('Z', '+00:00')).timestamp(), item['publicationId']))
        for item in group:
            if item['publicationId'] == keeper['publicationId']:
                continue
            identifier = item['publicationId']
            reasons[identifier].add('exact-duplicate')
            if identifier not in latest:
                reasons[identifier].add('old-release')
                candidate_class[identifier] = 'old-release'
            else:
                candidate_class[identifier] = 'exact-duplicate'
            matches[identifier].append({'publicationId': keeper['publicationId'], 'candidateClass': 'exact-duplicate',
                                        'scores': {'G': 100.0, 'P': 100.0, 'F': 100.0, 'M': 100.0, 'S': 100.0}})

    for left, right in combinations(parts, 2):
        left_id, right_id = left['publicationId'], right['publicationId']
        if left['templateId'] == right['templateId']:
            continue
        if candidate_class[left_id] in ('old-release', 'exact-duplicate') or candidate_class[right_id] in ('old-release', 'exact-duplicate'):
            continue
        left_payload, right_payload = payloads.get(left_id), payloads.get(right_id)
        if left_payload is None or right_payload is None or left['sha256'].lower() == right['sha256'].lower():
            continue
        comparison = compare_analyses(analyses[left_id], analyses[right_id])
        group = comparison['candidateClass']
        if group not in ('high-similarity', 'suspected-similarity'):
            continue
        for source, target in ((left_id, right_id), (right_id, left_id)):
            matches[source].append({'publicationId': target, 'candidateClass': group,
                                     'scores': comparison['scores'], 'differences': comparison['differences']})
            if candidate_class[source] not in ('old-release', 'exact-duplicate'):
                if candidate_class[source] == 'none' or group == 'high-similarity':
                    candidate_class[source] = group
                reasons[source].add(group)

    complete = not any(issue.get('severity') != 'warning' for issue in issues)
    invalid_latest_templates = {item['templateId'] for item in parts if item['publicationId'] in latest
                                and analyses.get(item['publicationId'], {}).get('candidateClass') != 'analyzed'}
    rows = []
    for item in items:
        kind = item.get('templateKind', 'part')
        identifier = item['publicationId']
        refs = references[kind, identifier]
        if kind == 'component':
            category = 'component-release'
        else:
            category = candidate_class[identifier]
            analysis = analyses.get(identifier, {})
            if analysis.get('candidateClass') == 'unclassified':
                category = 'unclassified'
        invalid_replacement = category == 'old-release' and item['templateId'] in invalid_latest_templates
        if (not complete or invalid_replacement) and category in ('old-release', 'exact-duplicate', 'high-similarity', 'suspected-similarity'):
            status = 'blocked'
        elif category in ('old-release', 'exact-duplicate'):
            status = 'auto-clean'
        elif category == 'high-similarity':
            status = 'awaiting-confirmation'
        elif category == 'suspected-similarity':
            status = 'notice'
        elif category == 'unclassified':
            status = 'unclassified'
        else:
            status = 'retained'
        item_reasons = sorted(reasons.get(identifier, set()))
        item_reasons.extend(sorted({ref['kind'] for ref in refs}))
        analysis = analyses.get(identifier, {})
        score_rows = [match for match in matches.get(identifier, []) if 'scores' in match]
        scores = max(score_rows, key=lambda row: row['scores']['S'])['scores'] if score_rows else None
        rows.append({**item, 'candidateClass': category, 'status': status, 'reasons': item_reasons,
                     'references': refs, 'scores': scores, 'matches': matches.get(identifier, []),
                     'similarityReason': 'LATEST_PACKAGE_UNCLASSIFIED' if invalid_replacement else analysis.get('reason')})

    strict = [item for item in rows if item['status'] == 'auto-clean']
    pending = [item for item in rows if item['status'] == 'awaiting-confirmation']
    review = [item for item in rows if item['status'] in ('auto-clean', 'awaiting-confirmation', 'notice')]
    return {'checkedAt': now.isoformat(), 'scanComplete': complete,
            'similarityVersion': 1, 'referenceScope': 'published-local-and-registered',
            'deletionEnabled': bool(getattr(storage, 'writable', False)) and complete,
            'sharedReferences': {'clientCount': len(shared), 'staleClientCount': stale,
                                 'coverageComplete': False, 'localStatus': registry.status() if registry else None},
            'summary': {'totalCount': len(rows), 'totalBytes': sum(item['size'] for item in rows),
                        'strictCandidateCount': len(strict),
                        'strictCandidateBytes': sum(item['size'] for item in strict),
                        'highSimilarityCount': len(pending),
                        'reviewCount': len(review),
                        'reviewBytes': sum(item['size'] for item in review),
                        'retainedCount': sum(item['status'] == 'retained' for item in rows),
                        'blockedCount': sum(item['status'] == 'blocked' for item in rows)},
            'items': rows, 'issues': issues}


router = APIRouter(prefix='/api/template-library', tags=['发布库存储检查'])


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scanId: str = Field(pattern=r'^[a-f0-9]{32}$')
    publicationIds: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(min_length=1, max_length=1000)


@router.post('/cleanup-preview', summary='扫描旧模板包占用和引用，仅生成清理预览')
async def preview_cleanup(request: Request):
    state = request.app.state
    report = await asyncio.to_thread(cleanup_preview, state.published_storage, state.drafts, state.publications,
                                     state.reference_registry, state.library_cache)
    report = await asyncio.to_thread(state.cleanup_store.save_scan, report)
    return JSONResponse(report, headers={'Cache-Control': 'no-store'})


async def _cleanup(request: Request, body: CleanupRequest):
    state = request.app.state
    scan_id, publication_ids = body.scanId, body.publicationIds
    report = await asyncio.to_thread(state.cleanup_store.load_scan, scan_id)
    if report is None:
        return JSONResponse({'error': {'code': 'CLEANUP_SCAN_NOT_FOUND'}}, status_code=404)
    if not report.get('scanComplete'):
        return JSONResponse({'scanId': scan_id, 'items': [
            {'publicationId': identifier, 'status': 'blocked', 'errorCode': 'CLEANUP_SCAN_INCOMPLETE'}
            for identifier in publication_ids]}, status_code=409, headers={'Cache-Control': 'no-store'})
    result = await asyncio.to_thread(execute_cleanup, state, report, publication_ids)
    status = 200 if all(item['status'] == 'deleted' for item in result['items']) else 409
    return JSONResponse(result, status_code=status, headers={'Cache-Control': 'no-store'})


@router.post('/cleanup', summary='按扫描结果清理已确认的模板包')
async def cleanup(request: Request, body: CleanupRequest):
    return await _cleanup(request, body)


@router.get('/backups', summary='列出本地清理备份')
async def list_backups(request: Request):
    return JSONResponse({'items': await asyncio.to_thread(request.app.state.cleanup_store.list_backups)}, headers={'Cache-Control': 'no-store'})


@router.get('/cleanup-audit', summary='导出清理审计记录')
async def export_audit(request: Request):
    entries = await asyncio.to_thread(request.app.state.cleanup_store.read_audit)
    return JSONResponse({'items': entries}, headers={'Cache-Control': 'no-store',
                        'Content-Disposition': 'attachment; filename="template-cleanup-audit.json"'})


@router.get('/backups/{backup_id}/download', summary='下载清理备份')
async def download_backup(request: Request, backup_id: str):
    backup = await asyncio.to_thread(request.app.state.cleanup_store.backup, backup_id)
    if backup is None:
        return JSONResponse({'error': {'code': 'BACKUP_NOT_FOUND'}}, status_code=404)
    manifest, package, _metadata = backup
    return Response(package, media_type='application/octet-stream', headers={
        'Content-Disposition': f'attachment; filename="{manifest["publicationId"]}.rwpart"',
        'X-RuiWare-SHA256': manifest['sha256'],
    })


@router.post('/backups/{backup_id}/restore', summary='恢复本地清理备份')
async def restore_backup(request: Request, backup_id: str):
    return await asyncio.to_thread(_restore_backup, request.app.state, backup_id)


def _restore_backup(state, backup_id: str):
    with state.cleanup_lock:
        return _restore_backup_locked(state, backup_id)


def _restore_backup_locked(state, backup_id: str):
    backup = state.cleanup_store.backup(backup_id)
    if backup is None:
        return JSONResponse({'error': {'code': 'BACKUP_NOT_FOUND'}}, status_code=404)
    manifest, package, metadata = backup
    object_key = manifest.get('objectKey')
    object_path = PurePosixPath(object_key) if isinstance(object_key, str) else PurePosixPath('.')
    prefix_parts = PurePosixPath(state.published_storage.settings.read_prefix).parts
    if (not isinstance(object_key, str) or '\\' in object_key or ':' in object_key or '..' in object_path.parts or
            manifest.get('templateKind') != 'part' or object_path.parts[:len(prefix_parts)] != prefix_parts or
            len(object_path.parts) != len(prefix_parts) + 3 or object_path.name != 'template.rwpart' or
            object_path.parent.name != manifest.get('publicationId')):
        return JSONResponse({'error': {'code': 'BACKUP_KEY_INVALID'}}, status_code=409)
    try:
        current = state.published_storage.record('part', manifest['publicationId'])
        if current.object_key != object_key or current.publication.sha256.lower() != manifest['sha256'].lower():
            return JSONResponse({'error': {'code': 'BACKUP_TARGET_EXISTS'}}, status_code=409)
    except LibraryError as error:
        if error.code != 'REMOTE_TEMPLATE_NOT_FOUND':
            return JSONResponse({'error': {'code': error.code}}, status_code=error.status)
    metadata_key = f"{object_path.parent.as_posix()}/metadata.json"
    audit = {'publicationId': manifest['publicationId'], 'backupId': backup_id, 'sha256': manifest['sha256'],
             'objectKey': object_key, 'createdAt': datetime.now(timezone.utc).isoformat()}
    try:
        state.cleanup_store.append_audit({**audit, 'status': 'restoring'})
        state.published_storage.restore_objects_if_absent([
            (object_key, package, 'application/octet-stream'),
            (metadata_key, metadata, 'application/json'),
        ])
    except LibraryError as error:
        try:
            state.cleanup_store.append_audit({**audit, 'status': 'restore-failed', 'errorCode': error.code})
        except OSError:
            pass
        return JSONResponse({'error': {'code': error.code}}, status_code=error.status)
    except OSError:
        return JSONResponse({'error': {'code': 'CLEANUP_AUDIT_FAILED'}}, status_code=503)
    try:
        state.cleanup_store.append_audit({**audit, 'status': 'restored'})
    except OSError:
        return {'backupId': backup_id, 'status': 'restored', 'publicationId': manifest['publicationId'],
                'warningCode': 'CLEANUP_AUDIT_FAILED'}
    return {'backupId': backup_id, 'status': 'restored', 'publicationId': manifest['publicationId']}


@router.delete('/backups/{backup_id}', summary='删除过期本地备份')
async def delete_backup(request: Request, backup_id: str):
    store = request.app.state.cleanup_store
    backup = await asyncio.to_thread(store.backup, backup_id)
    if backup is None:
        return JSONResponse({'error': {'code': 'BACKUP_NOT_FOUND'}}, status_code=404)
    if datetime.fromisoformat(backup[0]['expiresAt'].replace('Z', '+00:00')) > datetime.now(timezone.utc):
        return JSONResponse({'error': {'code': 'BACKUP_NOT_EXPIRED'}}, status_code=409)
    audit = {'backupId': backup_id, 'publicationId': backup[0]['publicationId'],
             'createdAt': datetime.now(timezone.utc).isoformat()}
    try:
        await asyncio.to_thread(store.append_audit, {**audit, 'status': 'deleting-backup'})
    except OSError:
        return JSONResponse({'error': {'code': 'CLEANUP_AUDIT_FAILED'}}, status_code=503)
    if not await asyncio.to_thread(store.delete_backup, backup_id):
        return JSONResponse({'error': {'code': 'BACKUP_NOT_FOUND'}}, status_code=404)
    try:
        await asyncio.to_thread(store.append_audit, {**audit, 'status': 'backup-deleted'})
    except OSError:
        return {'backupId': backup_id, 'status': 'deleted', 'warningCode': 'CLEANUP_AUDIT_FAILED'}
    return {'backupId': backup_id, 'status': 'deleted'}
