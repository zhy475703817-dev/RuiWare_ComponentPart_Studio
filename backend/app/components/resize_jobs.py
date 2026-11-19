"""Revision-scoped, cancellable previews and atomic application."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import time
from uuid import uuid4
from functools import wraps
from threading import RLock

from fastapi import APIRouter, Request
from pydantic import Field

from .models import ComponentModel, ComponentDocument, GeometryLock, GeometryRelation, Revision, Vector3
from .service import ComponentError, validate_component
from .resize_geometry import CadProvider
from .resize_solver import ResizeSolver, ResizeFailure, fingerprint


class ResizeDesignInput(ComponentModel):
    locks: list[GeometryLock] = Field(default_factory=list, max_length=1000)
    relations: list[GeometryRelation] = Field(default_factory=list, max_length=2000)


class ResizeRequest(ComponentModel):
    expectedRevision: Revision
    dimensions: Vector3 | None = None
    inspectOnly: bool = False
    design: ResizeDesignInput | None = None
    solutionIndex: int | None = Field(default=None, ge=0, le=63)


class ResizeApplyRequest(ComponentModel):
    expectedRevision: Revision
    jobId: str
    checksum: str


def serialized(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with self.guard:
            return function(self, *args, **kwargs)
    return call


class ResizeJobs:
    def __init__(self, app):
        self.app, self.jobs, self.tasks = app, {}, set()
        self.semaphore = asyncio.Semaphore(1)
        self.geometry_cache = {}
        self.guard = RLock()

    async def close(self):
        for job in self.jobs.values(): job["cancelled"] = True
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def get(self, draft_id, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            saved = self.app.state.drafts.resize_history(draft_id)
            if saved and saved["jobId"] == job_id: job = saved
        if job is None or job["draftId"] != draft_id:
            raise ComponentError(404, "RESIZE_JOB_NOT_FOUND")
        current = self.app.state.drafts.get(draft_id)["document"]["revision"]
        if current != job["expectedRevision"] and job["status"] not in ("applied", "cancelled"):
            job.update(status="stale", cancelled=True, message="草稿已变化，请重新试算")
        return job

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in ("cancelled", "before", "created")}

    @serialized
    def start(self, request, draft_id, body):
        draft = self.app.state.drafts.get(draft_id)["document"]
        if draft["revision"] != body.expectedRevision:
            raise ComponentError(409, "REVISION_CONFLICT")
        for id, job in list(self.jobs.items()):
            if time.monotonic() - job["created"] > 1800 and job["status"] != "running": self.jobs.pop(id)
            elif job["draftId"] == draft_id and job["status"] in ("running", "ready", "needs_choice"):
                job.update(cancelled=True, status="stale", message="已被新的试算替代")
        if len(self.jobs) >= 100:
            raise ComponentError(429, "RESIZE_JOB_LIMIT")
        id = str(uuid4())
        job = {"jobId": id, "draftId": draft_id, "expectedRevision": body.expectedRevision, "status": "running", "message": "正在准备模型", "cancelled": False, "created": time.monotonic(), "before": draft}
        self.jobs[id] = job
        task = asyncio.create_task(self.run(request, job, body.model_dump(exclude_none=True)))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return self.public(job)

    async def run(self, request, job, body):
        try:
            async with self.semaphore:
                if job["cancelled"]: return
                templates, publications = {}, {}
                from ..reconstruction import _read_template
                async def collect(document, ancestors=()):
                    if len(ancestors) > 16: raise ResizeFailure("组件嵌套超过16层", "RESIZE_NESTING")
                    for member in document["assembly"]["members"]:
                        if job["cancelled"]: raise ResizeFailure("已取消", "RESIZE_CANCELLED")
                        reference = member["template"]
                        identity = (reference["publicationId"], reference["sha256"])
                        if reference.get("templateKind") == "component":
                            if identity in ancestors: raise ResizeFailure("组件循环引用", "RESIZE_NESTING")
                            publication = await asyncio.to_thread(self.app.state.publications.detail, reference["publicationId"])
                            if any(publication[k] != reference[k] for k in ("templateId", "version", "sha256")):
                                raise ResizeFailure("子组件发布版本不一致", "RESIZE_REFERENCE")
                            publications[identity] = publication["document"]
                            await collect(member.get("instanceDocument") or publication["document"], (*ancestors, identity))
                        elif identity not in templates:
                            publication, draft, material = await _read_template(request, *identity)
                            if publication.templateId != reference["templateId"] or publication.version != reference["version"]:
                                raise ResizeFailure("零件发布版本不一致", "RESIZE_REFERENCE")
                            templates[identity] = draft, material
                await collect(job["before"])
                provider = CadProvider(templates)
                provider.cache = self.geometry_cache
                solver = ResizeSolver(job["before"], provider, publications, body, lambda: job["cancelled"], lambda message: job.update(message=message))
                result = await asyncio.to_thread(solver.solve)
                if job["cancelled"]: return
                if self.app.state.drafts.get(job["draftId"])["document"]["revision"] != job["expectedRevision"]:
                    job.update(status="stale", message="草稿已变化，请重新试算"); return
                if "document" in result:
                    document = ComponentDocument.model_validate(result["document"])
                    def validate_tree(doc, path=()):
                        issues = [{**c, "path": list(path)} for c in validate_component(doc) if c["status"] == "fail"]
                        for member in doc.assembly.members:
                            if member.instanceDocument: issues.extend(validate_tree(member.instanceDocument, (*path, member.id)))
                        return issues
                    failures = validate_tree(document)
                    if failures: raise ResizeFailure("联动结果未通过草稿检查", "RESIZE_VALIDATION", failures)
                    result["document"] = document.model_dump(by_alias=True)
                    result["checksum"] = fingerprint(result["document"])
                job.update(status="ready", message="预览完成，尚未应用" if not body["inspectOnly"] else "已读取当前实际尺寸", result=result)
        except asyncio.CancelledError:
            job.update(status="cancelled", cancelled=True, message="试算已取消")
        except Exception as error:
            if job["cancelled"]: return
            code = getattr(error, "code", "RESIZE_FAILED")
            job.update(status="needs_choice" if code == "RESIZE_NEEDS_CHOICE" else "failed", message=str(error) or "模型计算失败", error={"code": code, "details": getattr(error, "details", [])})

    @serialized
    def apply(self, draft_id, body):
        job = self.get(draft_id, body.jobId)
        if job["status"] != "ready" or "document" not in job.get("result", {}):
            raise ComponentError(409, "RESIZE_PREVIEW_UNAVAILABLE")
        if body.expectedRevision != job["expectedRevision"] or body.checksum != job["result"]["checksum"]:
            raise ComponentError(409, "RESIZE_PREVIEW_MISMATCH")
        result = self.app.state.drafts.replace_geometry(draft_id, body.expectedRevision, job["result"]["document"], history={"jobId": body.jobId, "checksum": body.checksum})
        job.update(status="applied", appliedRevision=result["document"]["revision"], message="几何联动已整体保存")
        return result

    @serialized
    def undo(self, draft_id, body):
        job = self.get(draft_id, body.jobId)
        if job["status"] != "applied" or job.get("appliedRevision") != body.expectedRevision or body.checksum != job["result"]["checksum"]:
            raise ComponentError(409, "RESIZE_UNDO_STALE")
        result = self.app.state.drafts.replace_geometry(draft_id, body.expectedRevision, job["before"])
        job.update(status="stale", message="已恢复应用前的组件")
        return result


router = APIRouter(prefix="/api/component-drafts", tags=["组件几何联动"])


@router.get("/{draft_id}/resize-history")
def history(request: Request, draft_id: str):
    repository = request.app.state.drafts
    document = repository.get(draft_id)["document"]
    saved = repository.resize_history(draft_id)
    return {"undo": {"jobId": saved["jobId"], "checksum": saved["result"]["checksum"], "revision": saved["appliedRevision"]} if saved and document["revision"] == saved["appliedRevision"] else None}


@router.post("/{draft_id}/resize-jobs", status_code=202)
async def start(request: Request, draft_id: str, body: ResizeRequest):
    return request.app.state.resize_jobs.start(request, draft_id, body)


@router.get("/{draft_id}/resize-jobs/{job_id}")
def get(request: Request, draft_id: str, job_id: str):
    return ResizeJobs.public(request.app.state.resize_jobs.get(draft_id, job_id))


@router.delete("/{draft_id}/resize-jobs/{job_id}")
def cancel(request: Request, draft_id: str, job_id: str):
    job = request.app.state.resize_jobs.get(draft_id, job_id)
    if job["status"] != "applied": job.update(cancelled=True, status="cancelled", message="试算已取消，原模型未改变")
    return ResizeJobs.public(job)


@router.post("/{draft_id}/resize-apply")
def apply(request: Request, draft_id: str, body: ResizeApplyRequest):
    return request.app.state.resize_jobs.apply(draft_id, body)


@router.post("/{draft_id}/resize-undo")
def undo(request: Request, draft_id: str, body: ResizeApplyRequest):
    return request.app.state.resize_jobs.undo(draft_id, body)
