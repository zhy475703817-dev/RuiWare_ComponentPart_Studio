# Persistent library cache implementation plan

**Goal:** Show the existing local library immediately after restart, synchronize MinIO in the background, and offer ready updates explicitly.

**Architecture:** Reuse SQLite beside the draft tables, partition caches by configured source and credentials (hash only). Store original packages, engine-versioned default models/checks, and active/pending catalog snapshots. Existing publishing/dependency checks remain remote-authoritative. Parts retain their existing upstream API as the MinIO access boundary.

**Spec:** User-approved design in this conversation: local first; first load progressively accepts checked templates; later changes require user action; unsupported parts stay hidden; network errors retain the previous catalog; fixed component references never change.

**Tech Stack:** Existing FastAPI, sqlite3, asyncio, React, TypeScript; no new dependencies.

## Task 1: Persistent cache and synchronization
- [x] Write integration tests for restart/offline model reads, incremental sync, pending/apply, invalid parts, transient failures, hash/engine/source isolation and empty/deleted remote catalogs.
- [x] Run tests to reproduce missing cache behavior.
- [x] Add `backend/app/library_cache.py` for SQLite package/model/catalog storage and source-isolated keys.
- [x] Add `backend/app/library_sync.py` for one background synchronization per backend, five-minute checks, independent part/component errors and GET/apply endpoints.
- [x] Integrate cached package/model reads in reconstruction and component viewing, preserving remote publication validation.
- [x] Run focused backend tests and correct failures.

## Task 2: Library interface and regression verification
- [x] Test the cached catalog client response, update application and invalid responses.
- [x] Switch `PublishedTemplateLibrary.tsx` to the cached catalog; poll while preparing, keep visible items during checks, offer an update button, and refresh automatically after own publication.
- [x] Verify existing backend/frontend tests and production build.
- [x] Exercise browser first load, reload, pending update, application and component publication loop; measure warm local response without claiming unmeasured production speedups.
- [x] Document cache location, local-only UI behavior, offline limitations and update rules in README.

Execute inline; preserve existing work and do not commit or write additional remote test releases.

## Verification results

- Backend: 67 tests passed (including 11 persistent-cache regressions).
- Frontend: 33 tests passed; TypeScript and Vite production build passed. Existing large-bundle warning remains.
- Browser against isolated MemoryMinio fixtures: first preparation, pending notification, explicit application, unchanged current edits and member reference, offline backend restart with cached default model, and zero browser exceptions passed.
- Separate browser publication smoke: A -> B -> C, idempotent publish, reload and package download passed. No real shared MinIO writes.
- Isolated warm local snapshot: 8 ms; zero package downloads or CAD runs after offline restart. This fixture measurement is not a production performance guarantee.
- Current development backend responded 200 for health and the new library API; its first real background cache preparation was started with a read-only request.
- Review corrections: queue forced checks during an existing scan, continue healthy package checks after a per-package failure, commit eviction of damaged cache entries, and validate current metadata even when the package/model was already cached.
