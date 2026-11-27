"""MCP entry points for the same validated editing actions used by the page."""
from typing import Annotated

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..components.models import (
    ComponentAction, TypedAction, Revision, ComponentMember, ComponentSlot, SlotPatch,
    ComponentParameter, ParameterBinding, ComponentPattern, ComponentRule, ComponentInterfaceType,
)


def register(mcp, app, invoke):
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    async def edit(draft_id, revision, action):
        return await invoke(app.state.drafts.update, draft_id, revision,
                            ComponentAction.validate_python(action), actor="agent")

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_apply_actions(draftId: str, expectedRevision: Revision,
        actions: Annotated[list[TypedAction], Field(min_length=1, max_length=100)]) -> CallToolResult:
        """原子执行1至100个草稿动作，只增加一次修订；任一步失败全部回滚。支持成员、连接、接口、槽位、参数、绑定、阵列、规则、模型恢复和重置；含删除/覆盖能力。"""
        return await invoke(app.state.drafts.update, draftId, expectedRevision, actions, actor="agent")

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_member(draftId: str, expectedRevision: Revision, member: ComponentMember) -> CallToolResult:
        """加入已准备好的固定发布版本成员数据；从发布库自动创建成员请用component_add_template_member。"""
        return await edit(draftId, expectedRevision, {"type": "member.add", "member": member})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_mate(draftId: str, expectedRevision: Revision, mateId: str) -> CallToolResult:
        """删除连接关系，保留成员接口和当前位置。"""
        return await edit(draftId, expectedRevision, {"type": "mate.remove", "id": mateId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_slot(draftId: str, expectedRevision: Revision, slot: ComponentSlot) -> CallToolResult:
        """新增空槽位；memberId/interfaceId应为空，可指定已有基准接口。"""
        return await edit(draftId, expectedRevision, {"type": "slot.add", "slot": slot})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_update_slot(draftId: str, expectedRevision: Revision, slotId: str, patch: SlotPatch) -> CallToolResult:
        """修改槽位名称、必填性、基准和占用；占用成员与接口需成对提交。"""
        return await edit(draftId, expectedRevision, {"type": "slot.patch", "id": slotId, "patch": patch})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_slot(draftId: str, expectedRevision: Revision, slotId: str) -> CallToolResult:
        """删除槽位定义，保留装配成员。"""
        return await edit(draftId, expectedRevision, {"type": "slot.remove", "id": slotId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_rename_interface(draftId: str, expectedRevision: Revision, portId: str, name: str) -> CallToolResult:
        """重命名组件对外接口。"""
        return await edit(draftId, expectedRevision, {"type": "port.rename", "id": portId, "name": name})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_interface(draftId: str, expectedRevision: Revision, portId: str) -> CallToolResult:
        """取消组件对外接口，保留成员原始接口。"""
        return await edit(draftId, expectedRevision, {"type": "port.remove", "id": portId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_interface_type(draftId: str, expectedRevision: Revision, memberId: str, interfaceId: str,
        connectionTypeId: str | None, customTypeLabel: str | None = None, connectableTypeIds: list[str] | None = None) -> CallToolResult:
        """设置接口连接类型；null清空。自定义类型可同时提供customTypeLabel及允许连接类型。"""
        action = {"type": "interface.type.set", "memberId": memberId, "interfaceId": interfaceId,
                  "connectionTypeId": connectionTypeId, "customTypeLabel": customTypeLabel}
        if connectableTypeIds is not None:
            action["connectableTypeIds"] = connectableTypeIds
        return await edit(draftId, expectedRevision, action)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_interface_connectable_types(draftId: str, expectedRevision: Revision, memberId: str,
        interfaceId: str, connectableTypeIds: Annotated[list[str], Field(max_length=50)]) -> CallToolResult:
        """设置成员接口允许连接的类型列表；空列表清空选择。"""
        return await edit(draftId, expectedRevision, {"type": "interface.connectable.set", "memberId": memberId,
            "interfaceId": interfaceId, "connectableTypeIds": connectableTypeIds})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_merge_interface_types(draftId: str, expectedRevision: Revision,
        types: Annotated[list[ComponentInterfaceType], Field(max_length=500)]) -> CallToolResult:
        """合并自定义接口类型目录；ID必须为custom:加类型名称，冲突时拒绝覆盖。"""
        return await edit(draftId, expectedRevision, {"type": "interface.types.merge", "types": types})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_parameter(draftId: str, expectedRevision: Revision, parameter: ComponentParameter) -> CallToolResult:
        """新增组件参数定义及初始值。"""
        return await edit(draftId, expectedRevision, {"type": "parameter.add", "parameter": parameter})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_parameter(draftId: str, expectedRevision: Revision, parameterId: str) -> CallToolResult:
        """删除组件参数及其关联绑定、阵列。"""
        return await edit(draftId, expectedRevision, {"type": "parameter.remove", "id": parameterId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_set_binding(draftId: str, expectedRevision: Revision, bindingId: str,
        binding: ParameterBinding | None) -> CallToolResult:
        """设置组件参数到成员参数的绑定；null移除指定绑定。几何模式受现有联动限制。"""
        return await edit(draftId, expectedRevision, {"type": "binding.set", "id": bindingId, "binding": binding})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_pattern(draftId: str, expectedRevision: Revision, pattern: ComponentPattern) -> CallToolResult:
        """新增成员阵列，使用已有数量参数及间距参数；几何模式请用几何联动。"""
        return await edit(draftId, expectedRevision, {"type": "pattern.add", "pattern": pattern})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_pattern(draftId: str, expectedRevision: Revision, patternId: str) -> CallToolResult:
        """删除成员阵列定义，保留原成员。"""
        return await edit(draftId, expectedRevision, {"type": "pattern.remove", "id": patternId})

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_rule(draftId: str, expectedRevision: Revision, rule: ComponentRule) -> CallToolResult:
        """新增规则说明，不执行表达式。"""
        return await edit(draftId, expectedRevision, {"type": "rule.add", "rule": rule})

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_remove_rule(draftId: str, expectedRevision: Revision, ruleId: str) -> CallToolResult:
        """删除指定规则说明。"""
        return await edit(draftId, expectedRevision, {"type": "rule.remove", "id": ruleId})
