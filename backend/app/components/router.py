from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from .models import ActionRequest, ComponentDocument, CreateDraftRequest, PublishRequest
from .service import serialize_component


router = APIRouter(prefix="/api/component-drafts", tags=["组件草稿"])
publication_router = APIRouter(prefix="/api/published-components", tags=["组件发布库"])


@publication_router.get("")
def list_publications(request: Request):
    return JSONResponse(request.app.state.publications.list(), headers={"Cache-Control": "no-store"})


@publication_router.get("/{publication_id}")
def publication(request: Request, publication_id: str):
    cached = request.app.state.library_cache.publication(publication_id, kind='component')
    if cached:
        payload = request.app.state.library_cache.package(cached)
        if payload is not None:
            return JSONResponse(request.app.state.publications._parse_package(payload, cached.model_dump(exclude_unset=True)), headers={"Cache-Control": "no-store"})
    return JSONResponse(request.app.state.publications.detail(publication_id), headers={"Cache-Control": "no-store"})


@publication_router.get("/{publication_id}/download")
def publication_download(request: Request, publication_id: str):
    cached = request.app.state.library_cache.publication(publication_id, kind='component')
    payload = request.app.state.library_cache.package(cached) if cached else None
    if payload is None:
        payload = request.app.state.publications.package(publication_id)
    return Response(payload, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{publication_id}.rwpart"', "Cache-Control": "no-store",
    })


@router.post("/{draft_id}/publish")
def publish_draft(request: Request, draft_id: str, body: PublishRequest):
    return JSONResponse(request.app.state.publications.publish(draft_id, body.expectedRevision), headers={"Cache-Control": "no-store"})


@router.get("")
def list_drafts(request: Request):
    return request.app.state.drafts.list()


@router.post("")
def create_draft(request: Request, body: CreateDraftRequest):
    return request.app.state.drafts.create(name=body.name, document=body.document)


@router.get("/{draft_id}")
def get_draft(request: Request, draft_id: str, afterRevision: int = Query(default=0, ge=0, le=9007199254740991)):
    return request.app.state.drafts.get(draft_id, after_revision=afterRevision)


@router.post("/{draft_id}/actions")
def update_draft(request: Request, draft_id: str, body: ActionRequest):
    return request.app.state.drafts.update(draft_id, body.expectedRevision, body.action)


@router.put("/{draft_id}/evidence/{evidence_id}")
async def upload_evidence(request: Request, draft_id: str, evidence_id: str, fileName: str = Query(max_length=255)):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > 5 * 1024 * 1024):
        return JSONResponse({"error": {"code": "EVIDENCE_TOO_LARGE"}}, status_code=413)
    content = await request.body()
    if len(content) > 5 * 1024 * 1024:
        return JSONResponse({"error": {"code": "EVIDENCE_TOO_LARGE"}}, status_code=413)
    media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    return request.app.state.drafts.save_evidence(draft_id, evidence_id, fileName, media_type, content)


@router.get("/{draft_id}/evidence/{evidence_id}")
def download_evidence(request: Request, draft_id: str, evidence_id: str):
    item = request.app.state.drafts.get_evidence(draft_id, evidence_id)
    safe_inline = item["mediaType"] in {"image/png", "image/jpeg", "image/gif", "image/webp"}
    disposition = "inline" if safe_inline else "attachment"
    return Response(item["content"], media_type=item["mediaType"], headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(item['fileName'], safe='')}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
    })


@router.delete("/{draft_id}/evidence/{evidence_id}", status_code=204)
def delete_evidence(request: Request, draft_id: str, evidence_id: str):
    request.app.state.drafts.delete_evidence(draft_id, evidence_id)
    return Response(status_code=204)


@router.get("/{draft_id}/validation")
def get_validation(request: Request, draft_id: str):
    draft = request.app.state.drafts.get(draft_id)
    return {"draftId": draft_id, "revision": draft["document"]["revision"], "checks": draft["checks"]}


@router.get("/{draft_id}/export")
def export_draft(request: Request, draft_id: str):
    exported = request.app.state.drafts.export(draft_id)
    document_name = exported.get("name", "component")
    return JSONResponse(exported, headers={
        "Content-Disposition": f"attachment; filename=component.json; filename*=UTF-8''{quote(document_name + '.json', safe='')}",
        "Cache-Control": "no-store",
    })
