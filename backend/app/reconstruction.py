from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

from fastapi import APIRouter, Query, Request, Response
import httpx
from pydantic import ValidationError

from .config import PROJECT_ROOT, Settings
from .library import LibraryError, PublishedTemplate
from .preview import DOWNLOAD_TIMEOUT_SECONDS, MAX_CACHE_BYTES, MAX_DOWNLOAD_BYTES, MAX_JSON_BYTES, SHA256, download, read_package, read_publication


ENGINE_ROOT = PROJECT_ROOT / "backend" / "engine"
WORKER_TIMEOUT_SECONDS = 90
MAX_REQUEST_BYTES = 64 * 1024


class ReconstructionCache:
    """Default models or confirmed package failures, bounded for this engine lifetime."""
    def __init__(self):
        self.entries: OrderedDict[tuple[str, str], bytes | bool] = OrderedDict()
        self.size = 0

    def get(self, publication: PublishedTemplate) -> bytes | bool | None:
        key = publication.publicationId, publication.sha256.lower()
        if key not in self.entries:
            return None
        self.entries.move_to_end(key)
        return self.entries[key]

    def put(self, publication: PublishedTemplate, model: bytes | bool):
        key = publication.publicationId, publication.sha256.lower()
        previous = self.entries.pop(key, False)
        self.size -= len(previous) if isinstance(previous, bytes) else 0
        weight = len(model) if isinstance(model, bytes) else 0
        if weight > MAX_CACHE_BYTES:
            return
        while self.entries and (self.size + weight > MAX_CACHE_BYTES or len(self.entries) >= 1000):
            _, removed = self.entries.popitem(last=False)
            self.size -= len(removed) if isinstance(removed, bytes) else 0
        self.entries[key] = model
        self.size += weight


def _package_documents(data: bytes, publication: PublishedTemplate) -> dict[str, dict[str, Any]]:
    read_package(data, publication, require_model=False)
    required = {
        "manifest": "manifest.json",
        "material": "material-validation.json",
        "parameters": "parameters.json",
        "recipe": "geometry-recipe.json",
        "rules": "feature-rules.json",
        "constraints": "constraints.json",
        "sweep": "sweep-path.json",
        "interfaces": "interfaces.json",
        "variants": "variants.json",
        "requirements": "material-requirements.json",
        "evidence": "evidence.json",
        "admission": "admission.json",
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            result = {}
            for key, filename in required.items():
                info = archive.getinfo(filename)
                if info.file_size > MAX_JSON_BYTES:
                    raise LibraryError(502, "PACKAGE_TOO_LARGE")
                value = json.loads(archive.read(info), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict):
                    raise ValueError
                result[key] = value
            return result
    except LibraryError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, RuntimeError, RecursionError):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED") from None


def _draft_and_material(documents: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = {key: value for key, value in documents["manifest"].items() if key not in {"reconstructionGuide", "previewModel"}}
    if not documents["recipe"].get("operations"):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED")
    manifest.update(
        parameterDefinitions=documents["parameters"].get("definitions"),
        geometryRecipe=documents["recipe"],
        featureRules=documents["rules"].get("rules", []),
        featureRulesReviewed=documents["rules"].get("reviewed", False),
        sketch=documents["constraints"],
        interfaces=documents["interfaces"].get("interfaces", []),
        sweepPath=None if documents["sweep"].get("status") == "empty" else documents["sweep"],
        variants=documents["variants"].get("variants", []),
        materialRequirements=documents["requirements"].get("requirements", []),
        blank=documents["requirements"].get("blank"),
        materialValidationSamples=documents["material"].get("samples", []),
        evidence=documents["evidence"].get("items", []),
        aiProposals=documents["evidence"].get("aiProposals", []),
        admission=documents["admission"].get("policy", {}),
    )
    try:
        from backend.engine.template_core import TemplateDraft

        draft = TemplateDraft.model_validate(manifest)
    except (ValidationError, TypeError, ValueError):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED") from None

    resolved = documents["material"].get("resolved", [])
    if not isinstance(resolved, list) or any(not isinstance(item, dict) for item in resolved):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED")
    preferred = next((item for item in draft.materialValidationSamples if item.role == "nominal"), None)
    preferred = preferred or next((item for item in draft.materialValidationSamples if item.requiredForAdmission), None)
    selected = next((item for item in resolved if preferred and item.get("sampleId") == preferred.id), None)
    material = selected.get("material") if isinstance(selected, dict) else None
    material_bound = any(item.sourceDefinition.type == "materialProperty" for item in draft.parameterDefinitions)
    if material_bound and not isinstance(material, dict):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED")
    snapshot = {"record": material or {}}
    if isinstance(selected, dict) and isinstance(selected.get("provenance"), dict):
        snapshot["provenance"] = selected["provenance"]
    return draft.model_dump(mode="json"), snapshot


def editable_parameters(draft_data: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from backend.engine.template_core import TemplateDraft

        draft = TemplateDraft.model_validate(draft_data)
    except (ValidationError, TypeError, ValueError):
        raise LibraryError(422, "PACKAGE_UNSUPPORTED") from None
    return [
        {
            "id": item.id,
            "label": item.displayName or item.label,
            "unit": item.unit,
            "valueType": item.valueType,
            "default": item.default,
            "minimum": item.minimum,
            "maximum": item.maximum,
            "allowedValues": item.allowedValues,
        }
        for item in draft.parameterDefinitions
        if item.exposed
        and item.sourceDefinition.type == "userInput"
        and item.scope in {"template", "partInstance"}
    ]


def resolved_interfaces(draft_data: dict[str, Any], material: dict[str, Any], overrides: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve interface occurrences with the same engine context used for model reconstruction."""
    from backend.engine.template_core import TemplateDraft
    from backend.engine.template_core.rules import evaluate_template

    draft = TemplateDraft.model_validate(draft_data)
    external_context = {
        "material": material.get("record", material),
        "product": material.get("product", {}),
        "component": material.get("component", {}),
        "projectZone": material.get("projectZone", {}),
        "standard": material.get("standard", {}),
    }
    evaluation = evaluate_template(
        draft.parameterDefinitions, draft.featureRules,
        overrides=overrides or {}, external_context=external_context,
        semantic_faces=draft.geometryRecipe.semanticFaces, interfaces=draft.interfaces,
    )
    return [item.model_dump(mode="json") for item in evaluation.resolvedInterfaces], [item.model_dump(mode="json") for item in evaluation.diagnostics]


def _run_worker(draft_data: dict[str, Any], material: dict[str, Any], overrides: dict[str, Any]) -> bytes:
    from backend.engine.template_core import TemplateDraft, lower_to_plan

    try:
        draft = TemplateDraft.model_validate(draft_data)
        for value in overrides.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError
        plan = lower_to_plan(draft, material, overrides=overrides)
    except (ValidationError, TypeError, ValueError):
        raise LibraryError(422, "PARAMETER_INVALID") from None
    if any(item.severity == "error" for item in plan.diagnostics):
        raise LibraryError(422, "PARAMETER_INVALID")

    runtime_root = PROJECT_ROOT / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="component-cad-", dir=runtime_root) as temporary:
        root = Path(temporary)
        plan_path, output_path, result_path = root / "plan.json", root / "cad", root / "result.json"
        plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(ENGINE_ROOT)}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "cad_worker.cli", "--plan", str(plan_path), "--output", str(output_path), "--result", str(result_path)],
                cwd=ENGINE_ROOT,
                env=env,
                capture_output=True,
                timeout=WORKER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise LibraryError(504, "MODEL_RECONSTRUCTION_FAILED") from None
        except OSError:
            raise LibraryError(503, "MODEL_RECONSTRUCTION_FAILED") from None
        if result.returncode == 2 and result_path.is_file():
            raise LibraryError(422, "MODEL_RECONSTRUCTION_FAILED") from None
        if result.returncode != 0 or not result_path.is_file():
            raise LibraryError(503, "MODEL_RECONSTRUCTION_FAILED") from None
        try:
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
            if result_data.get("success") is not True or result_data.get("metrics", {}).get("valid") is not True:
                raise ValueError("invalid geometry")
            stl_path = output_path / result_data["inputHash"][:16] / "preview.stl"
            stl = stl_path.read_bytes()
        except (OSError, KeyError, ValueError, TypeError):
            raise LibraryError(422, "MODEL_RECONSTRUCTION_FAILED") from None
        if not stl or len(stl) > MAX_DOWNLOAD_BYTES:
            raise LibraryError(422, "MODEL_RECONSTRUCTION_FAILED")
        return stl


async def _read_template(request: Request, publication_id: str, expected_hash: str | None, *, publication: PublishedTemplate | None = None):
    client, settings = request.app.state.http, request.app.state.settings
    store = request.app.state.library_cache
    storage = getattr(request.app.state, "published_storage", None)
    cache_enabled = storage is not None and storage.client is not None
    record = None
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            publication = publication or (store.publication(publication_id, expected_hash) if cache_enabled else None)
            if publication is None and storage is not None and storage.client is not None:
                record = await asyncio.to_thread(storage.record, "part", publication_id, expected_hash)
                publication = record.publication
            if publication is None:
                publication = await read_publication(client, settings, publication_id, expected_hash)
            if publication.size > MAX_DOWNLOAD_BYTES:
                raise LibraryError(502, "PACKAGE_TOO_LARGE")
            package = store.package(publication) if cache_enabled else None
            if package is None:
                if record is None and storage is not None and storage.client is not None:
                    record = await asyncio.to_thread(storage.record, "part", publication_id, publication.sha256)
                if record is not None:
                    package = await asyncio.to_thread(storage.package, record)
                else:
                    package = await download(client, settings, f"/api/public/v1/templates/{publication_id}/download", MAX_DOWNLOAD_BYTES)
            if len(package) != publication.size or hashlib.sha256(package).hexdigest() != publication.sha256.lower():
                raise LibraryError(502, "PACKAGE_INTEGRITY_ERROR")
    except (TimeoutError, httpx.TimeoutException):
        raise LibraryError(504, "LIBRARY_TIMEOUT") from None
    except httpx.RequestError:
        raise LibraryError(502, "LIBRARY_UNREACHABLE") from None
    documents = await asyncio.to_thread(_package_documents, package, publication)
    draft, material = await asyncio.to_thread(_draft_and_material, documents)
    return publication, draft, material


router = APIRouter(prefix="/api/published-templates", tags=["模板模型"])


def confirmed_unavailable(error: LibraryError) -> bool:
    package_error = error.code in {"PACKAGE_INVALID", "PACKAGE_TOO_LARGE"}
    model_error = error.status == 422 and error.code in {"PACKAGE_UNSUPPORTED", "PARAMETER_INVALID", "MODEL_RECONSTRUCTION_FAILED"}
    return package_error or model_error


@router.get("/{publication_id}/availability", summary="检查固定发布版本是否可生成模型")
async def template_availability(publication_id: str, request: Request, response: Response,
                                sha256: str = Query(pattern=SHA256)):
    store = request.app.state.library_cache
    storage = getattr(request.app.state, "published_storage", None)
    cache_enabled = storage is not None and storage.client is not None
    local = store.publication(publication_id, sha256) if cache_enabled else None
    if local:
        publication = local
    elif storage is not None and storage.client is not None:
        publication = (await asyncio.to_thread(storage.record, "part", publication_id, sha256)).publication
    else:
        publication = await read_publication(request.app.state.http, request.app.state.settings, publication_id, sha256)
    cache = request.app.state.reconstruction_cache
    async with request.app.state.reconstruction_slots:
        cached = cache.get(publication)
        if cached is None and cache_enabled and local:
            cached = store.model(publication)
        if cached is None:
            try:
                _, draft, material = await _read_template(request, publication_id, sha256, publication=publication)
                cached = await asyncio.to_thread(_run_worker, draft, material, {})
            except LibraryError as error:
                # Infrastructure failures are retryable; never remember them as bad templates.
                if not confirmed_unavailable(error):
                    raise
                cached = False
            cache.put(publication, cached)
            if cache_enabled and local:
                store.save_model(publication, cached)
    response.headers["Cache-Control"] = "no-store"
    return {"available": isinstance(cached, bytes) and bool(cached)}


@router.get("/{publication_id}/reconstruction", summary="读取模板参数")
async def reconstruction_info(publication_id: str, request: Request, response: Response,
                              sha256: str | None = Query(default=None, pattern=SHA256)):
    async with request.app.state.reconstruction_slots:
        publication, draft, material = await _read_template(request, publication_id, sha256)
    resolved, diagnostics = await asyncio.to_thread(resolved_interfaces, draft, material)
    response.headers["Cache-Control"] = "no-store"
    return {
        "publication": publication.model_dump(mode="json"),
        "parameters": editable_parameters(draft),
        "interfaces": draft.get("interfaces", []),
        "resolvedInterfaces": resolved,
        "interfaceDiagnostics": diagnostics,
    }


@router.post("/{publication_id}/reconstruction/model", summary="生成零件预览")
async def reconstruction_model(publication_id: str, request: Request, sha256: str = Query(pattern=SHA256)):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_REQUEST_BYTES:
        raise LibraryError(413, "PARAMETER_INVALID")
    body_bytes = bytearray()
    try:
        async for chunk in request.stream():
            if len(body_bytes) + len(chunk) > MAX_REQUEST_BYTES:
                raise LibraryError(413, "PARAMETER_INVALID")
            body_bytes.extend(chunk)
        body = json.loads(body_bytes)
    except LibraryError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise LibraryError(422, "PARAMETER_INVALID") from None
    if not isinstance(body, dict) or set(body) - {"parameters"}:
        raise LibraryError(422, "PARAMETER_INVALID")
    parameters = body.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 64 or any(not isinstance(key, str) or len(key) > 128 for key in parameters):
        raise LibraryError(422, "PARAMETER_INVALID")
    async with request.app.state.reconstruction_slots:
        publication, draft, material = await _read_template(request, publication_id, sha256)
        defaults = {item["id"]: item["default"] for item in editable_parameters(draft)}
        is_default = all(key in defaults and isinstance(value, bool) == isinstance(defaults[key], bool) and value == defaults[key] for key, value in parameters.items())
        cached = request.app.state.reconstruction_cache.get(publication) if is_default else None
        store = request.app.state.library_cache
        storage = getattr(request.app.state, "published_storage", None)
        cache_enabled = storage is not None and storage.client is not None
        local = store.publication(publication_id, sha256) if cache_enabled else None
        if is_default and cached is None and cache_enabled and local:
            cached = store.model(publication)
        stl = cached if isinstance(cached, bytes) else await asyncio.to_thread(_run_worker, draft, material, parameters)
        if is_default:
            request.app.state.reconstruction_cache.put(publication, stl)
            if cache_enabled and local:
                store.save_model(publication, stl)
    return Response(stl, media_type="model/stl", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-RuiWare-SHA256": hashlib.sha256(stl).hexdigest(),
        "X-RuiWare-Publication-SHA256": publication.sha256.lower(),
    })
