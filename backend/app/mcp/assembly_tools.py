from typing import Annotated, Literal

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from ..components import assembly
from ..components.models import Revision, Vector3, InterfaceSource
from ..components.resize_jobs import resize_history
from ..preview import SHA256


def register(mcp, app, invoke):
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_add_template_member(draftId: str, expectedRevision: Revision, publicationId: str,
        sha256: Annotated[str, Field(pattern=SHA256)], templateKind: Literal["part", "component"] = "part",
        position: Vector3 | None = None) -> CallToolResult:
        """从固定发布版本创建并加入零部件/子组件成员；读取参数、真实接口与自定义类型。position省略时按实际CAD边界自动放置，单位mm。"""
        return await invoke(assembly.add_template_member, app, draftId, expectedRevision, publicationId, sha256, templateKind, position)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_copy_member(draftId: str, expectedRevision: Revision, memberId: str) -> CallToolResult:
        """复制成员为独立实例，X方向平移40mm；保留参数与固定版本，不复制外部连接。"""
        return await invoke(assembly.copy_member, app, draftId, expectedRevision, memberId)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_ground_member(draftId: str, expectedRevision: Revision, memberId: str) -> CallToolResult:
        """按旋转后真实CAD最低点将成员贴地；已有连接的成员组整体平移，保存前检查修订。"""
        return await invoke(assembly.ground_member, app, draftId, expectedRevision, memberId)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_connect_interfaces(draftId: str, expectedRevision: Revision, fromMemberId: str,
        fromInterfaceId: str, toMemberId: str, toInterfaceId: str,
        relation: Literal["贴合", "同轴", "重合"] = "贴合") -> CallToolResult:
        """校验配合契约及占用，用明确真实CAD接口中心/法向自动对齐并记录连接。目标已有连接组整体变换，所有修改原子保存。无法定位真实接口时拒绝猜测。"""
        return await invoke(assembly.connect_interfaces, app, draftId, expectedRevision,
            InterfaceSource(memberId=fromMemberId, interfaceId=fromInterfaceId),
            InterfaceSource(memberId=toMemberId, interfaceId=toInterfaceId), relation)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_resize_history(draftId: str) -> CallToolResult:
        """读取当前修订可用的几何撤销任务、校验值及修订号；后端重启后仍可读取。"""
        return await invoke(resize_history, app.state.drafts, draftId)
