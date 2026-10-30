from __future__ import annotations

import hashlib
import json
from pathlib import Path

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.StlAPI import StlAPI_Writer

from template_core.models import Artifact, CanonicalPlan, Diagnostic

from .operators.body_ops import FaceMap, resolve_face_support


def _json_locator(locator):
    return locator.model_dump(mode="json") if hasattr(locator, "model_dump") else locator


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_compile_artifacts(
    *,
    shape,
    plan: CanonicalPlan,
    diagnostics: list[Diagnostic],
    job_directory: Path,
    plan_path: Path,
    public_prefix: str,
    face_map: FaceMap | None = None,
) -> list[Artifact]:
    step_path = job_directory / "model.step"
    stl_path = job_directory / "preview.stl"
    semantic_path = job_directory / "semantic-map.json"
    diagnostic_path = job_directory / "diagnostics.json"

    step_writer = STEPControl_Writer()
    if step_writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError("STEP transfer failed")
    if step_writer.Write(str(step_path)) != IFSelect_RetDone:
        raise RuntimeError("STEP write failed")
    BRepMesh_IncrementalMesh(shape, 0.35, False, 0.25, True).Perform()
    if not StlAPI_Writer().Write(shape, str(stl_path)):
        raise RuntimeError("STL write failed")

    semantic_faces: dict[tuple[str, str], dict] = {}

    def record_semantic_face(
        semantic_face_id,
        locator,
        operation_id: str,
        *,
        strict: bool = True,
    ) -> None:
        """Publish a stable semantic face once, then retain all consumers."""

        if not semantic_face_id:
            return
        if locator is None or face_map is None:
            key = (str(semantic_face_id), "legacy")
            entry = semantic_faces.setdefault(key, {
                "semanticFaceId": str(semantic_face_id),
                "sourceEntityId": None,
                "locator": None,
                "resolvedSupport": None,
                "resolution": "legacy-host-frame",
                "operationIds": [],
            })
        else:
            try:
                support = resolve_face_support(face_map, locator)
            except RuntimeError as error:
                if strict:
                    raise
                source_entity_id = str(
                    locator.get("sourceEntityId")
                    if isinstance(locator, dict)
                    else getattr(locator, "sourceEntityId", "")
                )
                key = (str(semantic_face_id), source_entity_id or "unresolved")
                entry = semantic_faces.setdefault(key, {
                    "semanticFaceId": str(semantic_face_id),
                    "sourceEntityId": source_entity_id or None,
                    "locator": _json_locator(locator),
                    "resolvedSupport": None,
                    "resolution": "unresolved",
                    "resolutionError": str(error),
                    "operationIds": [],
                })
                if operation_id not in entry["operationIds"]:
                    entry["operationIds"].append(operation_id)
                return
            key = (str(semantic_face_id), support.sourceEntityId)
            entry = semantic_faces.setdefault(key, {
                "semanticFaceId": str(semantic_face_id),
                "sourceEntityId": support.sourceEntityId,
                "locator": _json_locator(locator),
                "resolvedSupport": {
                    "operationId": support.operationId,
                    "profileSketchId": support.profileSketchId,
                    "kind": support.kind,
                    "capSide": support.capSide,
                    "origin": list(support.origin),
                    "uDirection": list(support.uDirection),
                    "vDirection": list(support.vDirection),
                    "normal": list(support.normal),
                    "supportFace": {
                        "type": "planar-face",
                        "resolved": True,
                    },
                },
                "operationIds": [],
            })
        if operation_id not in entry["operationIds"]:
            entry["operationIds"].append(operation_id)

    # The body operation carries the complete authored semantic-face list.
    # This keeps unreferenced semantic faces visible in the exported map.
    for operation in plan.operations:
        declarations = operation.arguments.get("semanticFaces") or []
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            record_semantic_face(
                declaration.get("semanticFaceId"),
                declaration.get("locator"),
                operation.id,
                strict=False,
            )

    # Manufacturing operations repeat the resolved face contract so consumers
    # can see which feature used each semantic face.  Keep that provenance too.
    for operation in plan.operations[1:]:
        record_semantic_face(
            operation.arguments.get("semanticFaceId"),
            operation.arguments.get("locator"),
            operation.id,
        )
    semantic_map = {
        "version": "1.0",
        "inputHash": plan.inputHash,
        "semanticFaces": list(semantic_faces.values()),
        "interfaces": [
            {"id": semantic_id, "sourceOperation": operation.id}
            for operation in plan.operations
            for semantic_id in operation.semanticOutputs
        ],
    }
    semantic_path.write_text(json.dumps(semantic_map, ensure_ascii=False, indent=2), encoding="utf-8")
    diagnostic_path.write_text(
        json.dumps([item.model_dump() for item in diagnostics], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    relative = job_directory.name
    paths = [
        ("step", step_path),
        ("stl", stl_path),
        ("plan", plan_path),
        ("semanticMap", semantic_path),
        ("diagnostics", diagnostic_path),
    ]
    return [
        Artifact(kind=kind, url=f"{public_prefix}/{relative}/{path.name}", sha256=sha256(path))
        for kind, path in paths
    ]
