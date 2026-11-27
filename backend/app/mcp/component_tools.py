from typing import Annotated

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..components.models import BraceGenerationRule, ComponentAction, ComponentDocument, DefinitionPatch, MemberPatch
from ..components.resize_jobs import ResizeRequest, ResizeApplyRequest, ResizeJobs
from .authoring import POLICY_VERSION, requirements
from ..components.qualification_policy import QualificationConfig


Revision = Annotated[int, Field(strict=True, ge=1)]
FiniteValue = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_cancel_authoring(draftId: str) -> CallToolResult:
        """暂停排队或运行中的自动制作验收，保留草稿；未通过时不能发布。继续制作后使用finish_authoring(retry=true)重试。"""
        async def cancel():
            return app.state.qualification_jobs.cancel(draftId)
        return await invoke(cancel)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_finish_authoring(draftId: str, expectedRevision: Revision, retry: bool = False) -> CallToolResult:
        """制作完成入口：平台自动执行原尺寸修复、增大/缩小/组合/孔列边界试调和复验。返回任务状态；通过前不能交付或发布，试验尺寸不覆盖原尺寸。"""
        async def start():
            from ..components.service import ComponentError
            if app.state.drafts.get(draftId)["document"]["revision"] != expectedRevision:
                raise ComponentError(409, "REVISION_CONFLICT")
            return app.state.qualification_jobs.queue(draftId, retry)
        return await invoke(start)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_authoring_status(draftId: str) -> CallToolResult:
        """读取自动制作验收进度、失败原因、测试点和deliveryReady；只有服务端当前修订验收通过才可称制作完成。"""
        async def read(): return app.state.qualification_jobs.status(draftId)
        return await invoke(read)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_configure_authoring(draftId: str, expectedRevision: Revision, config: QualificationConfig) -> CallToolResult:
        """声明需要支持的调整方向、最小/最大尺寸及额外测试点，并自动重新验收。不得缩减用户要求的范围或删除失败测试来冒充通过。"""
        return await invoke(lambda: app.state.drafts.configure_qualification(draftId, expectedRevision, config.model_dump()))

    async def edit(draft_id, revision, action):
        return await invoke(app.state.drafts.update, draft_id, revision, ComponentAction.validate_python(action), actor="agent")

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_authoring_requirements() -> CallToolResult:
        """AI每次制作组件前读取的默认要求：在选型、装配、参数配置时预防联动问题，修复后才交付。"""
        return await invoke(requirements)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_status() -> CallToolResult:
        """检查本组件服务和已实现能力；不表示上游发布库可用。"""
        return await invoke(lambda: {"status": "ok", "service": "component-template-api", "sharedDrafts": True,
                                    "libraryConfigured": app.state.settings.minio.read_configured or bool(app.state.settings.api_key.strip()),
                                    "publicationStorageConfigured": app.state.settings.minio.configured,
                                    "componentPublications": True, "assemblyEditing": True,
                                    "atomicActions": True, "libraryMaintenance": True,
                                    "geometryResize": True, "geometryPreviewRequiresApply": True,
                                    "templateAuthoringInfo": True, "authoringQualification": True,
                                    "publicationRequiresQualification": True, "publicationEvidence": True,
                                    "engineeringVerification": "not_implemented",
                                    "authoringPolicyVersion": POLICY_VERSION, "authoringRequirementsTool": "component_get_authoring_requirements",
                                    "pending": ["rules.execution"]})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_preview(draftId: str, request: ResizeRequest) -> CallToolResult:
        """制作中尽早试算真实CAD。inspectOnly只读尺寸；false且省略dimensions检查原尺寸完整联动。返回jobId，轮询status；失败需在制作时修复。试调不会保存。"""
        from types import SimpleNamespace
        async def start():
            return app.state.resize_jobs.start(SimpleNamespace(app=app), draftId, request)
        return await invoke(start)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_resize_status(draftId: str, jobId: str) -> CallToolResult:
        """读取几何任务状态、预览变更、校验值及需要用户选择的候选。"""
        return await invoke(lambda: ResizeJobs.public(app.state.resize_jobs.get(draftId, jobId)))

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_cancel(draftId: str, jobId: str) -> CallToolResult:
        """取消几何试算，保留原组件。"""
        def cancel():
            job = app.state.resize_jobs.get(draftId, jobId)
            if job['status'] != 'applied': job.update(cancelled=True, status='cancelled', message='试算已取消')
            return ResizeJobs.public(job)
        return await invoke(cancel)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_apply(draftId: str, request: ResizeApplyRequest) -> CallToolResult:
        """明确确认预览后，按revision、jobId、checksum原子应用几何结果。"""
        return await invoke(app.state.resize_jobs.apply, draftId, request, actor="agent")

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_undo(draftId: str, request: ResizeApplyRequest) -> CallToolResult:
        """恢复上次应用前的完整组件；草稿后来被修改时拒绝覆盖。"""
        return await invoke(app.state.resize_jobs.undo, draftId, request, actor="agent")

    @mcp.tool(annotations=write, structured_output=False)
    async def component_create_draft(name: str = "", document: ComponentDocument | None = None) -> CallToolResult:
        """按默认AI制作要求创建草稿；先读取真实模板接口和参数，装配时同步建立关系。柱片模板必须在制作时保存内置撑杆补齐规则及高度绑定，不等使用时配置。返回authoring早期检查项，需在制作中修复。允许保存中间草稿，不表示完成验收。"""
        return await invoke(app.state.drafts.create, name=name or None, document=document)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_list_drafts() -> CallToolResult:
        """列出共享草稿的标识、名称、修订号，定位需要编辑的草稿。"""
        return await invoke(app.state.drafts.list)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_draft(draftId: str) -> CallToolResult:
        """读取草稿、基础检查及authoring制作检查；取得revision，先解决可预防的接口、联动和参数问题。"""
        return await invoke(app.state.drafts.get, draftId)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_definition(draftId: str, expectedRevision: Revision, patch: DefinitionPatch) -> CallToolResult:
        """用 patch 修改组件名称、编码、类型或说明；只修改提供的字段。"""
        return await edit(draftId, expectedRevision, {"type": "definition.patch", "patch": patch.model_dump(exclude_unset=True)})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_parameter(draftId: str, expectedRevision: Revision, parameterId: str,
                                      value: FiniteValue | None) -> CallToolResult:
        """设置已有组件参数值（null表示未填写）；按参数及绑定成员范围约束，整数绑定按整数处理。不会触发CAD重建，几何模式部分尺寸必须走几何联动。"""
        return await edit(draftId, expectedRevision, {"type": "parameter.value", "id": parameterId, "value": value})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_rule(draftId: str, expectedRevision: Revision, ruleId: str, expression: str) -> CallToolResult:
        """更新已有规则的说明文字；不会执行表达式。"""
        return await edit(draftId, expectedRevision, {"type": "rule.expression", "id": ruleId, "expression": expression})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_member(draftId: str, expectedRevision: Revision, memberId: str, patch: MemberPatch) -> CallToolResult:
        """编辑成员角色、位置(mm)、旋转(rad)或参数；装配时同步维护真实连接和范围，处理返回的authoring检查，再用resize工具验证CAD。"""
        return await edit(draftId, expectedRevision, {"type": "member.patch", "id": memberId, "patch": patch.model_dump(exclude_unset=True)})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False), structured_output=False)
    async def component_remove_member(draftId: str, expectedRevision: Revision, memberId: str) -> CallToolResult:
        """删除已有成员，同时清理其连接、对外接口、参数绑定并清空槽位占用。"""
        return await edit(draftId, expectedRevision, {"type": "member.remove", "id": memberId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_mate(draftId: str, expectedRevision: Revision, fromMemberId: str, fromInterfaceId: str,
                                 toMemberId: str, toInterfaceId: str,
                                 relation: Annotated[str, Field(pattern="^(贴合|同轴|重合)$")] = "贴合", offset: FiniteValue = 0) -> CallToolResult:
        """装配时同步记录真实接口的连接关系；不自动移动成员。不得随意配对来消除提示，需用resize试算验证。"""
        from uuid import uuid4
        return await edit(draftId, expectedRevision, {"type": "mate.add", "mate": {
            "id": str(uuid4()), "from": {"memberId": fromMemberId, "interfaceId": fromInterfaceId},
            "to": {"memberId": toMemberId, "interfaceId": toInterfaceId}, "relation": relation, "offset": offset}})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_brace_generation_rule(draftId: str, expectedRevision: Revision, rule: BraceGenerationRule) -> CallToolResult:
        """将柱片增高后按新孔位补齐横撑、斜撑的要求保存为可执行规则。先读取真实成员、参数和接口；横撑与斜撑作为固定规格原型，每根原型仅由一条规则使用，多规则可共用立柱。需要同规格原型时新增独立成员并引用同一发布模板，不能引用生成实例。孔列模式须含一个{index}或{index:03d}占位符，横撑与斜撑孔距均引用正数mm参数。仅要求孔位几何定位时设置connectionMode=geometry，不声明工程配合通过；connectionMode=mates要求来源契约兼容并遵守接口容量。目标高度按规则增删实例，孔位、范围或规则冲突时明确失败，不拉伸撑杆。"""
        return await edit(draftId, expectedRevision, {"type": "brace.rule.set", "rule": rule.model_dump()})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_inherit_interface(draftId: str, expectedRevision: Revision, memberId: str, interfaceId: str,
                                          name: str, tags: list[str] | None = None) -> CallToolResult:
        """将已有成员的指定接口继承为组件对外接口。"""
        from uuid import uuid4
        return await edit(draftId, expectedRevision, {"type": "port.add", "port": {
            "id": str(uuid4()), "name": name, "source": {"memberId": memberId, "interfaceId": interfaceId}, "tags": tags or []}})

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_validate_draft(draftId: str) -> CallToolResult:
        """制作过程中和交付前验证草稿；返回基础checks及authoring早期问题。pending不算通过，自填verified不算CAD证据；按要求试调并修复后交付。"""
        def validate():
            draft = app.state.drafts.get(draftId)
            return {**draft, "revision": draft["document"]["revision"]}
        return await invoke(validate)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_export_draft(draftId: str) -> CallToolResult:
        """导出草稿JSON；新几何模式使用component-1.1并保留各层实例，旧草稿仍为1.0。"""
        def export():
            return app.state.drafts.export(draftId)
        return await invoke(export)
