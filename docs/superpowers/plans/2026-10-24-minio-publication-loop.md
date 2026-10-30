# Shared MinIO Component Publication Implementation Plan

**Goal:** Publish component `.rwpart` packages to the existing MinIO bucket and reuse them from independent component workspaces.

**Architecture:** SQLite keeps editable drafts and immutable prepared snapshots for retries. MinIO is the source of truth for the shared publication list, details, downloads and component dependencies. Use the existing `RUIWARE_MINIO_*` configuration and `publishers/<member>/<publicationId>/` layout; commit metadata only after the package upload.

**Tech Stack:** FastAPI, SQLite, official MinIO Python client, React/TypeScript.

**Spec:** User-approved shared bucket design in this conversation. Intermediate assembly/modeling stages remain incomplete.

## Constraints
- Preserve existing uncommitted work, old package bytes, publication IDs and pinned hashes.
- Distinguish components from parts in shared metadata; legacy untyped packages remain parts.
- No success or shared-library fallback when upload fails; keep the draft and retry the same snapshot.
- Never expose credentials to the frontend or logs. No separate component bucket or credentials.
- No automatic upload of local history during startup. Provide an explicit idempotent migration command.

## 1. Shared component storage
- [x] Add API integration tests using a shared in-memory S3 client and independent SQLite files: publish A, read/download A from B, nest A in B and publish B.
- [x] Verify tests fail against local-only publication behavior.
- [x] Add MinIO settings/client and component publication service, prepare snapshots locally, upload package then metadata and verify remote contents. Require remote dependencies and `.rwpart` downloads.
- [x] Test failed uploads, retry/idempotency, corrupted payloads, absent configuration and immutable historical migration.

## 2. Shared library compatibility
- [x] Add failing mixed-kind frontend and parts API tests.
- [x] Keep component metadata in public API; exclude components from part CAD checks and deduplicate by kind and publication ID.
- [x] Make parts storage deduplication type-aware and retain component publication IDs across member codes/revisions.

## 3. Migration and verification
- [x] Add explicit migration command preserving old package bytes/hashes and dependency order.
- [x] Update README/config examples with shared environment-variable behavior and publication failure semantics.
- [x] Run component backend/frontend tests, parts compatibility tests, build and diff checks.
- [x] Exercise independent backend publication/reuse on real MinIO with a unique temporary prefix. The account lacks delete permission, so isolated smoke objects need administrator cleanup; no existing objects were modified.

Execution is inline in the current workspace; user has already authorized implementation. Do not commit unrelated existing changes.

## Verification results
- Component backend: 56 tests passed; frontend: 32 tests passed; production build passed.
- Parts-platform compatibility: 47 tests passed.
- Browser: A → B → C reuse, reload, idempotent publication and `.rwpart` download passed.
- Real MinIO: independent SQLite workspaces published, listed, downloaded and nested shared components successfully; package hashes matched.
- Follow-up code review found no remaining actionable issues after both regression fixes.

## Temporary MinIO objects requiring administrator cleanup

The current account receives `AccessDenied` for `DeleteObject`. Four temporary test releases (eight objects: one package and one metadata file per release) remain in bucket `ruiware-templates`. No existing production objects were changed. An administrator should remove objects only under these exact temporary prefixes:

```text
publishers/YHR/smoke-471ef2af94a24b1fa5216bd25f7a6808/
publishers/YHR/smoke-672974bb2b084b2f810517553bb01178/
publishers/YHR/smoke-ee486eb0d67c44319db15bc764763d08/
```

These test releases can appear in the shared library until cleanup. Do not remove the parent `publishers/YHR/` prefix or unrelated releases.
