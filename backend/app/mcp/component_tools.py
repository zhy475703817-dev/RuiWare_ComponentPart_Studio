from typing import Annotated

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..components.models import ComponentAction, ComponentDocument, DefinitionPatch, MemberPatch
from ..components.resize_jobs import ResizeRequest, ResizeApplyRequest, ResizeJobs


Revision = Annotated[int, Field(strict=True, ge=1)]
FiniteValue = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    async def edit(draft_id, revision, action):
        return await invoke(app.state.drafts.update, draft_id, revision, ComponentAction.validate_python(action), actor="agent")

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_status() -> CallToolResult:
        """检查本组件服务和已实现能力；不表示上游发布库可用。"""
        return await invoke(lambda: {"status": "ok", "service": "component-template-api", "sharedDrafts": True,
                                    "libraryConfigured": bool(app.state.settings.api_key.strip()),
                                    "geometryResize": True, "geometryPreviewRequiresApply": True,
                                    "pending": ["rules.execution"]})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_preview(draftId: str, request: ResizeRequest) -> CallToolResult:
        """与参数页面共用的真实CAD几何试算；返回任务编号，不修改草稿。多解时需提交选择。"""
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
        return await invoke(app.state.resize_jobs.apply, draftId, request)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_resize_undo(draftId: str, request: ResizeApplyRequest) -> CallToolResult:
        """恢复上次应用前的完整组件；草稿后来被修改时拒绝覆盖。"""
        return await invoke(app.state.resize_jobs.undo, draftId, request)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_create_draft(name: str = "", document: ComponentDocument | None = None) -> CallToolResult:
        """创建持久化草稿；name 省略或为空时使用默认名称/导入文档名称。可导入符合 ComponentDocument 的工程文档。"""
        return await invoke(app.state.drafts.create, name=name or None, document=document)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_list_drafts() -> CallToolResult:
        """列出共享草稿的标识、名称、修订号，定位需要编辑的草稿。"""
        return await invoke(app.state.drafts.list)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_draft(draftId: str) -> CallToolResult:
        """读取共享草稿及当前检查；写操作前从结果取得 revision。"""
        return await invoke(app.state.drafts.get, draftId)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_definition(draftId: str, expectedRevision: Revision, patch: DefinitionPatch) -> CallToolResult:
        """用 patch 修改组件名称、编码、类型或说明；只修改提供的字段。"""
        return await edit(draftId, expectedRevision, {"type": "definition.patch", "patch": patch.model_dump(exclude_unset=True)})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_parameter(draftId: str, expectedRevision: Revision, parameterId: str,
                                      value: FiniteValue | None) -> CallToolResult:
        """设置已有组件参数值（null 表示未填写）；范围问题由验证报告，不会触发几何重建。"""
        return await edit(draftId, expectedRevision, {"type": "parameter.value", "id": parameterId, "value": value})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_rule(draftId: str, expectedRevision: Revision, ruleId: str, expression: str) -> CallToolResult:
        """更新已有规则的说明文字；不会执行表达式。"""
        return await edit(draftId, expectedRevision, {"type": "rule.expression", "id": ruleId, "expression": expression})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_member(draftId: str, expectedRevision: Revision, memberId: str, patch: MemberPatch) -> CallToolResult:
        """用 patch 编辑已有成员的角色、位置(mm)、旋转(rad)或参数覆盖；记录数据，不重建模型。"""
        return await edit(draftId, expectedRevision, {"type": "member.patch", "id": memberId, "patch": patch.model_dump(exclude_unset=True)})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False), structured_output=False)
    async def component_remove_member(draftId: str, expectedRevision: Revision, memberId: str) -> CallToolResult:
        """删除已有成员，同时清理其连接、对外接口、参数绑定并清空槽位占用。"""
        return await edit(draftId, expectedRevision, {"type": "member.remove", "id": memberId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_mate(draftId: str, expectedRevision: Revision, fromMemberId: str, fromInterfaceId: str,
                                 toMemberId: str, toInterfaceId: str,
                                 relation: Annotated[str, Field(pattern="^(贴合|同轴|重合)$")] = "贴合", offset: FiniteValue = 0) -> CallToolResult:
        """记录已有成员接口间的连接关系；尚未进行装配求解，不自动改变成员位置。"""
        from uuid import uuid4
        return await edit(draftId, expectedRevision, {"type": "mate.add", "mate": {
            "id": str(uuid4()), "from": {"memberId": fromMemberId, "interfaceId": fromInterfaceId},
            "to": {"memberId": toMemberId, "interfaceId": toInterfaceId}, "relation": relation, "offset": offset}})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_inherit_interface(draftId: str, expectedRevision: Revision, memberId: str, interfaceId: str,
                                          name: str, tags: list[str] | None = None) -> CallToolResult:
        """将已有成员的指定接口继承为组件对外接口。"""
        from uuid import uuid4
        return await edit(draftId, expectedRevision, {"type": "port.add", "port": {
            "id": str(uuid4()), "name": name, "source": {"memberId": memberId, "interfaceId": interfaceId}, "tags": tags or []}})

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_validate_draft(draftId: str) -> CallToolResult:
        """验证当前持久化草稿；pass/fail/pending 区分基础数据检查与尚未实现的工程检查。"""
        def validate():
            draft = app.state.drafts.get(draftId)
            return {"draftId": draftId, "revision": draft["document"]["revision"], "checks": draft["checks"]}
        return await invoke(validate)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_export_draft(draftId: str) -> CallToolResult:
        """导出草稿JSON；新几何模式使用component-1.1并保留各层实例，旧草稿仍为1.0。"""
        def export():
            return app.state.drafts.export(draftId)
        return await invoke(export)
