"""Revision-scoped, cancellable previews and atomic application."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from copy import deepcopy
from math import isclose
import time
from uuid import uuid4
from functools import wraps
from threading import RLock

from fastapi import APIRouter, Request
from pydantic import Field

from .models import ComponentModel, ComponentDocument, GeometryLock, GeometryRelation, Revision, Vector3
from .service import ComponentError, validate_component
from .resize_geometry import CadProvider
from .resize_solver import ResizeSolver, ResizeFailure, fingerprint, structure_changes
from .qualification_policy import continuation_dimensions


class ResizeDesignInput(ComponentModel):
    locks: list[GeometryLock] = Field(default_factory=list, max_length=1000)
    relations: list[GeometryRelation] = Field(default_factory=list, max_length=2000)


class ResizeRequest(ComponentModel):
    expectedRevision: Revision
    dimensions: Vector3 | None = None
    inspectOnly: bool = False
    autoFillBraces: bool | None = None
    design: ResizeDesignInput | None = None
    solutionIndex: int | None = Field(default=None, ge=0, le=63)
    retryFailedTarget: bool = False


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


async def load_geometry(request, document, cancelled, cache, *, include_hull=True):
    templates, publications = {}, {}
    from ..reconstruction import _read_template
    jobs = request.app.state.resize_jobs
    async def collect(doc, ancestors=()):
        if len(ancestors) > 16: raise ResizeFailure("组件嵌套超过16层", "RESIZE_NESTING")
        for member in doc["assembly"]["members"]:
            if cancelled(): raise ResizeFailure("已取消", "RESIZE_CANCELLED")
            reference = member["template"]
            identity = (reference["publicationId"], reference["sha256"])
            if reference.get("templateKind") == "component":
                if identity in ancestors: raise ResizeFailure("组件循环引用", "RESIZE_NESTING")
                publication = await jobs.resource(("component", *identity), lambda: asyncio.to_thread(request.app.state.publications.detail, reference["publicationId"]))
                if any(publication[k] != reference[k] for k in ("templateId", "version", "sha256")):
                    raise ResizeFailure("子组件发布版本不一致", "RESIZE_REFERENCE")
                publications[identity] = publication["document"]
                await collect(member.get("instanceDocument") or publication["document"], (*ancestors, identity))
            elif identity not in templates:
                # Bind the loop's identity before scheduling: resource loaders
                # can outlive this iteration while another request shares them.
                try:
                    publication, draft, material = await jobs.resource(("part", *identity), lambda identity=identity: _read_template(request, *identity))
                except Exception as error:
                    from ..library import LibraryError
                    if not isinstance(error, LibraryError):
                        raise
                    raise ResizeFailure(f"无法读取 {member.get('name', member['id'])} 的固定发布来源：{error.code}",
                                        error.code, [{"memberId": member['id'], "publicationId": identity[0],
                                                      "sha256": identity[1], "code": error.code}]) from error
                if publication.templateId != reference["templateId"] or publication.version != reference["version"]:
                    raise ResizeFailure("零件发布版本不一致", "RESIZE_REFERENCE")
                templates[identity] = draft, material
    await collect(document)
    provider = CadProvider(templates)
    provider.cache = cache
    provider.include_hull = include_hull
    return provider, publications


def inspection_key(document):
    """Keep all measurement inputs; hardware and revision do not change geometry."""
    source = deepcopy(document)
    def normalize(doc):
        doc.pop("revision", None)
        for port in doc.get("ports", []):
            # Ports resolve through source; the editable snapshot is not CAD input.
            port.pop("interface", None)
        for member in doc["assembly"]["members"]:
            for interface in member.get("interfaces", []): interface.pop("hardware", None)
            if member.get("instanceDocument"): normalize(member["instanceDocument"])
    normalize(source)
    return fingerprint(source)


def member_states(document, prefix=()):
    states = {}
    for member in document.get("assembly", {}).get("members", []):
        path = (*prefix, member["id"])
        states[path] = {
            "name": member.get("name", member["id"]),
            "state": {key: deepcopy(member[key]) for key in ("parameterValues", "position", "rotation") if key in member},
        }
        if member.get("instanceDocument"):
            states.update(member_states(member["instanceDocument"], path))
    return states


def restore_change_origins(document, changes, result_document):
    def same_state(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(same_state(a[key], b[key]) for key in a)
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(same_state(x, y) for x, y in zip(a, b))
        if isinstance(a, bool) or isinstance(b, bool):
            return a is b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return isclose(a, b, rel_tol=0, abs_tol=1e-7)
        return a == b

    original = member_states(document)
    current = member_states(result_document)
    added_paths = [tuple(item['path']) for item in structure_changes(document, result_document)['added']]
    changes[:] = [change for change in changes
                  if tuple(change['path']) in current
                  and not any(tuple(change['path'])[:len(path)] == path for path in added_paths)
                  and (tuple(change['path']) not in original
                       or not same_state(original[tuple(change['path'])]['state'], current[tuple(change['path'])]['state']))]
    by_path = {tuple(change.get("path", [])): change for change in changes}
    for path, source in original.items():
        result = current.get(path)
        if result is None or same_state(source["state"], result["state"]):
            continue
        change = by_path.get(path)
        if change is None:
            change = {"path": list(path), "name": source["name"]}
            changes.append(change)
            by_path[path] = change
        change.update(name=source["name"], before=source["state"], after=result["state"])
    return changes


def solve_with_recovery(document, provider, publications, body, cancelled, progress, inspected=None):
    """Use the same bounded continuation available during authoring trials."""
    def solve(source, request):
        return ResizeSolver(source, provider, publications, request, cancelled, progress,
            (lambda result: inspected(source, result)) if inspected else None).solve()
    source_measurement = None
    if (document.get("braceGenerationRules") and body.get("dimensions")
            and not body.get("inspectOnly")):
        source_measurement = solve(document, {"inspectOnly": True})
    try:
        result = solve(document, body)
        if source_measurement is not None:
            result["originalDimensions"] = source_measurement["dimensions"]
            result["changes"] = restore_change_origins(document, result.get("changes", []), result["document"])
        if 'document' in result:
            result['structureChanges'] = structure_changes(document, result['document'])
        return result
    except ResizeFailure as error:
        if (not body.get("dimensions") or body.get("inspectOnly")
                or error.code not in ("RESIZE_NO_SOLUTION", "RESIZE_INTERFACE_MISSING", "RESIZE_CONTACT_LOST")):
            raise
        initial_failure = error
    progress("正在保留原连接和锁定，尝试分步求解")
    measured = solve(document, {**body, "inspectOnly": True})
    midpoint = continuation_dimensions(measured["dimensions"], body["dimensions"], brace_mode_document(document, body.get('autoFillBraces')))
    if midpoint is None:
        raise initial_failure
    intermediate = solve(document, {**body, "dimensions": midpoint})
    design = intermediate["document"]["geometryDesign"]
    result = solve(intermediate["document"], {**body, "design": {"locks": design["locks"], "relations": design["relations"]}})
    changes = {tuple(c["path"]): deepcopy(c) for c in intermediate["changes"]}
    for change in result["changes"]:
        key = tuple(change["path"])
        if key in changes: changes[key]["after"] = change["after"]
        else: changes[key] = change
    restored = restore_change_origins(document, list(changes.values()), result["document"])
    result.update(changes=restored, originalDimensions=measured["dimensions"], automaticRecovery=True,
                  structureChanges=structure_changes(document, result['document']))
    return result


def brace_mode_document(document, enabled):
    if enabled is None:
        return document
    changed = deepcopy(document)
    for rule in changed.get('braceGenerationRules', []):
        rule['enabled'] = enabled
    return changed


class ResizeJobs:
    def __init__(self, app):
        self.app, self.jobs, self.tasks = app, {}, set()
        self.semaphore = asyncio.Semaphore(1)
        self.geometry_cache = {}
        self.guard = RLock()
        self.resources = OrderedDict()
        self.resource_tasks = {}
        self.inspections = OrderedDict()

    async def resource(self, key, loader):
        if key in self.resources:
            self.resources.move_to_end(key)
            return self.resources[key]
        if key not in self.resource_tasks:
            async def read():
                try:
                    value = await loader()
                    self.resources[key] = value
                    if len(self.resources) > 64: self.resources.popitem(last=False)
                    return value
                finally: self.resource_tasks.pop(key, None)
            task = asyncio.create_task(read())
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self.resource_tasks[key] = task
        return await asyncio.shield(self.resource_tasks[key])

    @serialized
    def remember_inspection(self, document, result):
        key = inspection_key(document)
        self.inspections[key] = deepcopy(result)
        self.inspections.move_to_end(key)
        if len(self.inspections) > 32: self.inspections.popitem(last=False)

    @asynccontextmanager
    async def geometry_turn(self, key, cancelled):
        if key is not None:
            while True:
                if cancelled(): raise ResizeFailure("已取消", "RESIZE_CANCELLED")
                with self.guard:
                    cached = deepcopy(self.inspections.get(key))
                if cached is not None:
                    yield cached
                    return
                try:
                    await asyncio.wait_for(self.semaphore.acquire(), timeout=.1)
                    break
                except TimeoutError: pass
        else:
            await self.semaphore.acquire()
        try: yield None
        finally: self.semaphore.release()

    async def close(self):
        for job in self.jobs.values(): job["cancelled"] = True
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for task in list(self.resource_tasks.values()): task.cancel()
        await asyncio.gather(*list(self.resource_tasks.values()), return_exceptions=True)

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
        if body.inspectOnly:
            key = inspection_key(draft)
            if key in self.inspections:
                self.inspections.move_to_end(key)
                job.update(status="ready", message="已读取当前实际尺寸", result=deepcopy(self.inspections[key]))
                return self.public(job)
        if not body.inspectOnly and body.dimensions is not None:
            error = self.app.state.drafts.check_resize_target(draft_id, brace_mode_document(draft, body.autoFillBraces), body.dimensions,
                body.design.model_dump() if body.design else None, body.retryFailedTarget)
            if error:
                job.update(status="failed", message=error["message"], error=error)
                return self.public(job)
        task = asyncio.create_task(self.run(request, job, body.model_dump(exclude_none=True)))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return self.public(job)

    async def run(self, request, job, body):
        try:
            key = inspection_key(job["before"]) if body["inspectOnly"] else None
            async with self.geometry_turn(key, lambda: job["cancelled"]) as measured:
                if job["cancelled"]: return
                # A preceding measurement or qualification may have completed while queued.
                if measured is not None:
                    result = measured
                else:
                    provider, publications = await load_geometry(request, job["before"], lambda: job["cancelled"], self.geometry_cache, include_hull=not body["inspectOnly"])
                    if job["cancelled"]: return
                    worker = asyncio.create_task(asyncio.to_thread(solve_with_recovery, job["before"], provider, publications, body,
                        lambda: job["cancelled"], lambda message: job.update(message=message), self.remember_inspection))
                    try: result = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        job["cancelled"] = True
                        await asyncio.gather(worker, return_exceptions=True)
                        raise
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
                if body["inspectOnly"]: self.remember_inspection(job["before"], result)
                if body.get("dimensions") and not body.get("inspectOnly"):
                    from .qualification_policy import design_key
                    self.app.state.drafts.forget_resize_failure(job["draftId"], job["expectedRevision"], design_key(brace_mode_document(job["before"], body.get('autoFillBraces')), body.get("design")), body["dimensions"])
        except asyncio.CancelledError:
            job.update(status="cancelled", cancelled=True, message="试算已取消")
        except Exception as error:
            if job["cancelled"]: return
            code = getattr(error, "code", "RESIZE_FAILED")
            job.update(status="needs_choice" if code == "RESIZE_NEEDS_CHOICE" else "failed", message=str(error) or "模型计算失败", error={"code": code, "details": getattr(error, "details", [])})
            if body.get("dimensions") and not body.get("inspectOnly"):
                from .qualification_policy import design_key
                self.app.state.drafts.record_resize_failure(job["draftId"], job["expectedRevision"],
                    design_key(brace_mode_document(job["before"], body.get('autoFillBraces')), body.get("design")), body["dimensions"], {**job["error"], "message": job["message"]})

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
