# MCP Shared Drafts Implementation Plan

> **For agentic workers:** execute sequentially in this session; use test-driven-development for state and protocol boundaries, and requesting-code-review for final review.

**Goal:** expose implemented component capabilities through MCP and share persisted drafts with the web workspace.

**Architecture:** FastAPI REST and mounted MCP call the same Python service and SQLite repository. The web workspace saves typed actions with optimistic concurrency and polls only when idle.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, official MCP Python SDK 1.x, SQLite, existing React/TypeScript/Node 24.

**Spec:** `docs/superpowers/specs/2026-10-20-mcp-shared-drafts-design.md`

## Global Constraints

- Preserve `component-1.0` export, version pinning, mm/radians, pending checks, upstream credential isolation, and existing preview integrity checks.
- Do not advertise unsolved assembly, executable rules, real publication, or template-to-member mapping.
- Every mutation requires an expected revision; no silent retry on conflict.
- Work in the authorized existing checkout; leave changes reviewable without pushing or merging.

## Task 1: Shared document service and REST

Files: `backend/app/components/{models,service,repository,router}.py`, `backend/app/main.py`, `backend/app/config.py`, `backend/tests/test_components.py`.

- [x] Add API tests: create -> edit -> stale edit returns 409 -> reread keeps first value; reopen same database preserves document; invalid reference rejected; deletion clears mates/ports/bindings/slots; export and checks match existing domain fixtures.
- [x] Run `.venv/Scripts/python.exe -m unittest backend.tests.test_components -v` and observe missing route failures.
- [x] Implement `DraftRepository(path)`: `create(name=None, document=None)`, `list()`, `get(draft_id)`, `update(draft_id, expected_revision, action)`; updates validate and commit atomically. Service supplies `apply_action`, `validate_component`, `serialize_component`.
- [x] Expose `GET/POST /api/component-drafts`, `GET /{draftId}`, `POST /{draftId}/actions` with `{expectedRevision, action}`, `GET /{draftId}/validation`, `GET /{draftId}/export`. Envelope `{draftId, document, checks}`.
- [x] Verify tests and compatibility with Node domain operations on shared test fixtures.

## Task 2: MCP transport and tools

Files: `backend/app/mcp/{server,library_tools,component_tools}.py`, `backend/app/{main,library,preview,config}.py`, `backend/requirements.txt`, `backend/tests/test_mcp.py`.

- [x] Add HTTP MCP tests: initialize, tools/list, tools/call create + update; read updated value via REST; stale tools/call returns error without mutation; configured bearer token blocks missing/wrong token.
- [x] Run tests and observe missing MCP endpoint failure.
- [x] Mount FastMCP at `/mcp` before static mount, run session manager inside FastAPI lifespan, share repository/client/cache. Register explicit business tools with JSON schemas and structured results, propagate safe business error codes as `isError` results.
- [x] Extract preview service dependencies from Request into arguments; provide version-pinned absolute model URLs. Reuse bounded upstream reads and declare search coverage.
- [x] Verify protocol, security boundaries, upstream error mapping, and static production routing.

## Task 3: Shared web workspace

Files: `src/app/{sharedDraft, useComponentWorkspace,App,WorkspaceLayout}.ts[x]`, tests `tests/shared-draft.test.mjs`.

- [x] Add controller tests with in-memory transport for rapid edits, serialized writes, polling, stale revision and failure recovery, missing saved draft, switching/new draft race, and disposal.
- [x] Run `node --test tests/shared-draft.test.mjs` and observe missing controller failure.
- [x] Implement a framework-free controller plus React subscription; optimistic local projection uses existing reducer, while authoritative document/checks/export come from service. Keep failed edits visible, suspend further changes on save error, offer explicit retry/reload; do not overwrite them with polls.
- [x] Add draft selector and shareable draft URL; preserve library/preview and seven-stage behavior. Export fetches the persisted server export after pending saves.
- [x] Run Node tests and `npm run build`.

## Task 4: Delivery and verification

Files: `.env.example`, `README.md`, `docs/{MCP,CODE-ARCHITECTURE}.md`.

- [x] Document endpoint, connection JSON, tool examples, draft selection/sync, token setup, database and scope limitations.
- [x] Run Python discovery, `npm test`, production build, protocol smoke check over a real temporary HTTP server. Request independent code review, address meaningful issues, and rerun covering checks.
- [x] Record completed steps and give the endpoint and user-facing behavior in final delivery.

## Verification result (2026-10-20)

- Backend: 29 unittest checks passed, including the official SDK over real HTTP, concurrent SQLite revisions, and all tool paths.
- Frontend: 22 Node checks passed; TypeScript and Vite production build passed.
- Real Chrome: MCP-created draft, web save, reload persistence, AI polling, selection, conflict preservation, and explicit reload passed with no page errors.
- Independent review: two reproduced issues fixed and re-reviewed (startup draft retry and literal JSON-looking MCP strings).
- Kept the authorized current checkout and did not push, merge, or create commits.
- Existing tracked production assets rebuilt. SDK constrained to tested major/minimum version mcp>=1.30,<2.
