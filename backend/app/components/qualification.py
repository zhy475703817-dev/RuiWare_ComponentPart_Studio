"""Automatic, cancellable CAD qualification; never applies trial dimensions."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, Request

from .models import ComponentDocument
from .qualification_policy import (QUALIFICATION_VERSION, QualificationRequest, QualificationConfigRequest,
                                   brace_height_grids, brace_height_supported, continuation_dimensions,
                                   design_key, digest, scenario_plan)
from .resize_jobs import load_geometry
from .resize_solver import ResizeSolver, ResizeFailure
from .service import ComponentError, validate_component
from .brace_policy import column_frame_members


class QualificationJobs:
    def __init__(self, app, debounce=5):
        self.app, self.repository, self.debounce = app, app.state.drafts, debounce
        self.loop = asyncio.get_running_loop()
        self.jobs, self.tasks = {}, set()
        self.closed = False

    def notify(self, draft_id):
        if not self.closed:
            self.loop.call_soon_threadsafe(self.queue, draft_id, False, self.debounce)

    def queue(self, draft_id, retry=False, delay=0):
        document = self.repository.get(draft_id)["document"]
        saved = self.repository.qualification(draft_id)
        active = self.jobs.get(draft_id)
        key = (document["revision"], digest(document), digest(saved["config"]))
        if active and active["key"] == key and not active["cancelled"] and active["report"]["status"] in ("queued", "running"):
            return self.public(active)
        if not retry and saved["status"] in ("passed", "failed", "needs_input", "cancelled"):
            return saved
        if active: active["cancelled"] = True
        report = {"jobId": str(uuid4()), "revision": key[0], "inputHash": key[1], "configHash": key[2],
                  "policyVersion": QUALIFICATION_VERSION, "status": "queued", "message": "等待编辑完成后自动验收",
                  "cases": [], "automaticRecoveries": 0}
        job = {"key": key, "document": document, "config": saved["config"], "report": report, "cancelled": False, "draftId": draft_id}
        self.jobs[draft_id] = job
        self.repository.save_qualification(draft_id, report)
        task = asyncio.create_task(self.run(job, delay))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return self.public(job)

    def public(self, job):
        return {**deepcopy(job["report"]), "draftId": job["draftId"], "config": job["config"],
                "deliveryReady": job["report"]["status"] == "passed"}

    def status(self, draft_id):
        saved = self.repository.qualification(draft_id)
        job = self.jobs.get(draft_id)
        if job and not job["cancelled"] and saved.get("jobId") == job["report"]["jobId"]:
            return self.public(job)
        return saved

    def cancel(self, draft_id):
        job = self.jobs.get(draft_id)
        if job and job["report"]["status"] in ("queued", "running"):
            job["cancelled"] = True
            job["report"].update(status="cancelled", message="已暂停本次验收；草稿可继续编辑，暂不能发布")
            try: self.repository.save_qualification(draft_id, job["report"])
            except ComponentError: pass
        return self.repository.qualification(draft_id)

    async def close(self):
        self.closed = True
        for job in self.jobs.values(): job["cancelled"] = True
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def check(self, job):
        if job["cancelled"] or self.closed or self.jobs.get(job["draftId"]) is not job:
            raise ResizeFailure("制作验收已取消", "RESIZE_CANCELLED")
        current = self.repository.qualification(job["draftId"])
        if current.get("jobId") != job["report"]["jobId"] or current["status"] == "stale":
            raise ResizeFailure("草稿已变化，将验收新修订", "RESIZE_CANCELLED")

    async def geometry_work(self, job, operation):
        """Queue constructor CAD and solving together, off the HTTP event loop."""
        semaphore = self.app.state.resize_jobs.semaphore
        while True:
            self.check(job)
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=.1)
                break
            except TimeoutError:
                pass
        try:
            self.check(job)
            worker = asyncio.create_task(asyncio.to_thread(operation))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                job['cancelled'] = True
                await asyncio.gather(worker, return_exceptions=True)
                raise
        finally:
            semaphore.release()
        self.check(job)
        return result

    async def solve(self, job, document, provider, publications, dimensions=None, inspect=False):
        self.check(job)
        request = {"inspectOnly": inspect}
        if dimensions is not None: request["dimensions"] = dimensions
        def calculate():
            solver = ResizeSolver(document, provider, publications, request, lambda: job["cancelled"] or self.closed,
                                  lambda text: job["report"].update(detail=text),
                                  lambda result: self.app.state.resize_jobs.remember_inspection(document, result))
            return solver.solve()
        result = await self.geometry_work(job, calculate)
        if "document" in result:
            def validate_tree(doc):
                failures = [c for c in validate_component(doc) if c["status"] == "fail"]
                for member in doc.assembly.members:
                    if member.instanceDocument: failures.extend(validate_tree(member.instanceDocument))
                return failures
            parsed = ComponentDocument.model_validate(result["document"])
            failures = validate_tree(parsed)
            if failures: raise ResizeFailure("联动结果未通过草稿检查", "RESIZE_VALIDATION", failures)
            result["document"] = parsed.model_dump(by_alias=True)
        elif inspect:
            self.app.state.resize_jobs.remember_inspection(document, result)
        return result

    async def verify_builtin_braces(self, job, nominal, provider, publications):
        """The usage switch cannot bypass qualification of a frame's capability.

        Exercise a private enabled copy and never save its mode or trial sizes.
        """
        if column_frame_members(nominal) is None:
            return
        source = deepcopy(nominal)
        for rule in source.get('braceGenerationRules', []):
            rule['enabled'] = True
        job['report'].update(message='验收模板内置补齐：原尺寸、增高与缩短')
        baseline = await self.solve(job, source, provider, publications)
        source = baseline['document']
        cases, _, _ = scenario_plan(baseline['dimensions'], [2], job['config'], source)
        trials = [{'label': '内置补齐 · 原尺寸', 'dimensions': baseline['dimensions'], 'status': 'pending'},
                  *[{**case, 'label': '内置补齐 · ' + case['label']} for case in cases]]
        job['report']['cases'].extend(trials)
        for index, case in enumerate(trials):
            case['status'] = 'running'
            self.repository.save_qualification(job['draftId'], job['report'])
            result = baseline if index == 0 else await self.solve(job, source, provider, publications, case['dimensions'])
            original = {m['id']: m for m in source['assembly']['members']}
            current = {m['id']: m for m in result['document']['assembly']['members']}
            for rule in source['braceGenerationRules']:
                for key in ('horizontalMemberId', 'diagonalMemberId'):
                    identifier = rule[key]
                    if current[identifier]['parameterValues'] != original[identifier]['parameterValues']:
                        raise ResizeFailure('内置补齐验收失败：撑杆规格被拉伸', 'AUTHORING_BRACE_SPEC_CHANGED')
            case.update(status='passed', actualDimensions=result['dimensions'], memberCount=len(current))

    @staticmethod
    def axes(baseline, solver, config):
        if config["axes"] is not None:
            axes = set(config["axes"])
            if config["minimumDimensions"] is not None:
                axes.update(i for i, (a, b) in enumerate(zip(config["minimumDimensions"], config["maximumDimensions"])) if b-a > .1)
            return sorted(axes)
        bound = {(tuple(b["path"]), b["memberParameterId"]) for b in solver.bindings}
        influences = baseline.get("influences", [])
        selected = [i for i in influences if (tuple(i["path"]), i["parameterId"]) in bound] or influences
        axes = set()
        for influence in selected:
            rotation = solver.by_path[tuple(influence["path"])]["world"][:3, :3]
            for movement in influence["movements"]:
                axes.update(int(a) for a in np.flatnonzero(np.abs(rotation @ movement["perUnit"]) > 1e-5))
        if config["minimumDimensions"] is not None:
            axes.update(i for i, (a, b) in enumerate(zip(config["minimumDimensions"], config["maximumDimensions"])) if b-a > .1)
        return sorted(axes)

    @staticmethod
    def add_boundary_cases(cases, baseline, solver, provider, axes, lower, upper):
        """Probe either side of retained linear-array end margins, where counts change.

        This is sampling, not a proof that every size between samples is feasible.
        """
        margins = getattr(provider, "generation_margins", None)
        if margins is None: return
        required = {solver.endpoint(r[s]) for r in baseline["relations"] for s in ("a", "b")}
        seen = {tuple(round(v, 5) for v in c["dimensions"]) for c in cases}
        height_grids = brace_height_grids(solver.document)
        added = 0
        for leaf in solver.leaves:
            member, path = leaf["member"], leaf["path"]
            geometry = provider.evaluate(member["template"], member["parameterValues"])
            local_axis = int(np.argmax(geometry.bounds[1]-geometry.bounds[0]))
            direction = np.abs(leaf["world"][:3, local_axis]); axis = int(np.argmax(direction))
            if axis not in axes or direction[axis] < .999999: continue
            occurrences = [i for i in geometry.resolved if (path, i["id"]) in required]
            for values in margins(member["template"], member["parameterValues"], occurrences).values():
                if values[1] <= .1: continue
                for delta in (-.1, .1):
                    target = list(baseline["dimensions"]); target[axis] -= float(values[1])+delta
                    key = tuple(round(v, 5) for v in target)
                    if key in seen or not lower[axis] <= target[axis] <= upper[axis]: continue
                    if not brace_height_supported(target[2], height_grids): continue
                    cases.append({"label": "孔列数量变化附近 · "+"XYZ"[axis], "dimensions": target, "status": "pending"})
                    seen.add(key); added += 1
                    if added >= 4: return

    async def run(self, job, delay):
        report = job["report"]
        try:
            if delay: await asyncio.sleep(delay)
            self.check(job)
            report.update(status="running", message="检查原尺寸、真实接口和约束")
            self.repository.save_qualification(job["draftId"], report)
            draft = self.repository.get(job["draftId"])
            failures = [c for c in draft["checks"] if c["status"] == "fail"]
            if failures: raise ResizeFailure("请先处理基础数据错误", "AUTHORING_DATA_INVALID", failures)
            provider, publications = await load_geometry(SimpleNamespace(app=self.app), job["document"], lambda: job["cancelled"], self.app.state.resize_jobs.geometry_cache)
            baseline = await self.solve(job, job["document"], provider, publications)
            nominal = baseline["document"]
            baseline_hash = digest(nominal)
            def plan_cases():
                solver = ResizeSolver(nominal, provider, publications, {}, lambda: job['cancelled'] or self.closed)
                axes = self.axes(baseline, solver, job["config"])
                cases, lower, upper = scenario_plan(baseline["dimensions"], axes, job["config"], nominal)
                self.add_boundary_cases(cases, baseline, solver, provider, axes, lower, upper)
                return axes, cases, lower, upper
            axes, cases, lower, upper = await self.geometry_work(job, plan_cases)
            report.update(axes=axes, nominalDimensions=baseline["dimensions"], minimumDimensions=lower, maximumDimensions=upper,
                          rangeIsExplicit=job["config"]["minimumDimensions"] is not None,
                          scope="测试点通过不代表区间内任意尺寸均可实现；新尺寸仍需真实CAD预览。",
                          nominalRepairCount=len(baseline["changes"]),
                          cases=[{"label": "原尺寸联动与修复复验", "dimensions": baseline["dimensions"], "status": "passed"}, *cases])
            for index, case in enumerate(cases, 1):
                self.check(job)
                report.update(message=f'自动试调 {index}/{len(cases)}：{case["label"]}')
                case["status"] = "running"
                self.repository.save_qualification(job["draftId"], report)
                try:
                    attempts = 1
                    try:
                        result = await self.solve(job, nominal, provider, publications, case["dimensions"])
                    except ResizeFailure as error:
                        if error.code not in ("RESIZE_NO_SOLUTION", "RESIZE_INTERFACE_MISSING", "RESIZE_CONTACT_LOST"): raise
                        # A bounded continuation retry keeps all relations, locks,
                        # source versions and limits. Never invent or drop a mate.
                        attempts = 3
                        report.update(message=f'自动修复并复验：{case["label"]}')
                        midpoint = continuation_dimensions(baseline["dimensions"], case["dimensions"], nominal)
                        if midpoint is None:
                            raise
                        intermediate = await self.solve(job, nominal, provider, publications, midpoint)
                        result = await self.solve(job, intermediate["document"], provider, publications, case["dimensions"])
                        report["automaticRecoveries"] += 1
                    case.update(status="passed", attempts=attempts, actualDimensions=result["dimensions"],
                                verification=result["document"]["geometryDesign"]["verification"])
                    self.repository.forget_resize_failure(job["draftId"], report["revision"], design_key(job["document"]), case["dimensions"])
                except ResizeFailure as error:
                    if error.code == "RESIZE_CANCELLED": raise
                    case.update(status="failed", error={"code": error.code, "message": str(error), "details": error.details})
                    self.repository.record_resize_failure(job["draftId"], report["revision"], design_key(job["document"]), case["dimensions"], case["error"])
                self.repository.save_qualification(job["draftId"], report)
                await asyncio.sleep(0)
            await self.verify_builtin_braces(job, nominal, provider, publications)
            if any(c["status"] != "passed" for c in report["cases"]):
                report.update(status="failed", message="部分使用尺寸未通过；已尝试约束内修复，需处理失败项后才能发布")
                self.repository.save_qualification(job["draftId"], report)
                return
            if digest(nominal) != baseline_hash: raise ResizeFailure("试调意外修改了原尺寸副本", "AUTHORING_BASELINE_CHANGED")
            # The candidate was fully CAD-verified before every independent trial;
            # remeasure the same unchanged nominal document before the atomic save.
            verified = await self.solve(job, nominal, provider, publications, inspect=True)
            if not np.allclose(verified["dimensions"], baseline["dimensions"], atol=.1, rtol=0):
                raise ResizeFailure("原尺寸复核不一致", "AUTHORING_BASELINE_CHANGED")
            self.check(job)
            report.update(status="passed", message="制作验收通过，已保存原尺寸及联动关系，可发布", detail="")
            saved = self.repository.save_qualification(job["draftId"], report, nominal)
            report.update({k: v for k, v in saved.items() if k not in ("config", "deliveryReady")})
        except asyncio.CancelledError:
            # Persisted queued/running reports resume after restart; never certify.
            pass
        except Exception as error:
            if job["cancelled"] or getattr(error, "code", None) == "RESIZE_CANCELLED": return
            code = getattr(error, "code", "AUTHORING_FAILED")
            source_errors = {
                "LIBRARY_INVALID_RESPONSE": "发布库返回的数据格式不正确，尚未进入几何验收；请检查发布库连接后重新验收。",
                "LIBRARY_UNREACHABLE": "无法连接发布库，尚未完成几何验收；恢复连接后重新验收。",
                "LIBRARY_TIMEOUT": "读取发布模型超时，请恢复连接后重新验收。",
                "COMPONENT_LIBRARY_NOT_CONFIGURED": "尚未配置零件发布库，请先配置连接再重新验收。",
                "PACKAGE_INTEGRITY_ERROR": "来源模型包校验失败，请恢复正确发布版本后重新验收。",
            }
            report.update(status="needs_input" if getattr(error, "code", "") == "RESIZE_NEEDS_CHOICE" else "failed",
                          message=source_errors.get(code, str(error) if isinstance(error, (ResizeFailure, ValueError)) else "制作验收未完成，请重试"),
                          error={"code": code, "details": getattr(error, "details", [])})
            try: self.repository.save_qualification(job["draftId"], report)
            except ComponentError: pass


router = APIRouter(prefix="/api/component-drafts", tags=["自动制作验收"])


@router.get("/{draft_id}/qualification")
async def status(request: Request, draft_id: str):
    return request.app.state.qualification_jobs.status(draft_id)


@router.post("/{draft_id}/qualification")
async def start(request: Request, draft_id: str, body: QualificationRequest):
    if request.app.state.drafts.get(draft_id)["document"]["revision"] != body.expectedRevision:
        raise ComponentError(409, "REVISION_CONFLICT")
    return request.app.state.qualification_jobs.queue(draft_id, body.retry)


@router.put("/{draft_id}/qualification/config")
async def configure(request: Request, draft_id: str, body: QualificationConfigRequest):
    return request.app.state.drafts.configure_qualification(draft_id, body.expectedRevision, body.config.model_dump())


@router.delete("/{draft_id}/qualification")
async def cancel(request: Request, draft_id: str):
    return request.app.state.qualification_jobs.cancel(draft_id)
