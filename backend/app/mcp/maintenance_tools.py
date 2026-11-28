"""MCP adapters for the shared library maintenance operations."""
from typing import Annotated
from urllib.parse import quote

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from .. import library_cleanup
from ..components.service import ComponentError


BackupId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


def register(mcp, app, invoke):
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_library() -> CallToolResult:
        """读取本地发布库缓存、同步进度和错误；不触发同步或自动清理。"""
        return await invoke(app.state.library_sync.snapshot)

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_sync_library() -> CallToolResult:
        """启动网页同款后台发布库同步；该流程会自动备份并清理符合条件的旧包。返回当前进度，使用component_get_library轮询。"""
        return await invoke(app.state.library_sync.check, force=True)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_apply_library() -> CallToolResult:
        """将后台已准备的待应用发布库列表切换为活动列表。"""
        return await invoke(app.state.library_sync.apply)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_preview_library_cleanup() -> CallToolResult:
        """只扫描发布包占用、引用和清理候选，保存24小时有效的scanId，不删除包。"""
        return await invoke(library_cleanup.scan_cleanup, app.state)

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_cleanup_library(request: library_cleanup.CleanupRequest) -> CallToolResult:
        """按已确认的scanId和publicationIds备份后清理；复核引用、版本、账号与扫描完整性，部分失败也标为工具错误。"""
        result = await invoke(library_cleanup.clean_selected, app.state, request)
        if not result.isError:
            result.isError = library_cleanup.cleanup_failed(result.structuredContent)
        return result

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_list_backups() -> CallToolResult:
        """列出清理备份及到期时间。"""
        return await invoke(library_cleanup.backup_list, app.state.cleanup_store)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_backup(backupId: BackupId) -> CallToolResult:
        """校验备份并返回元数据、SHA-256及绝对包下载地址，不返回大型二进制内容。"""
        def get():
            backup = app.state.cleanup_store.backup(backupId)
            if backup is None:
                raise ComponentError(404, "BACKUP_NOT_FOUND")
            return {**backup[0], "downloadUrl": app.state.settings.public_base_url.rstrip("/") +
                    f"/api/template-library/backups/{quote(backupId, safe='')}/download"}
        return await invoke(get)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_restore_backup(backupId: BackupId) -> CallToolResult:
        """恢复清理备份；目标存在不同内容时拒绝覆盖，并记录审计。"""
        return await invoke(library_cleanup.restore_saved_backup, app.state, backupId)

    @mcp.tool(annotations=destructive, structured_output=False)
    async def component_delete_backup(backupId: BackupId) -> CallToolResult:
        """删除已到期的本地备份；未到期备份拒绝删除。"""
        return await invoke(library_cleanup.remove_backup, app.state.cleanup_store, backupId)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_cleanup_audit() -> CallToolResult:
        """读取发布库清理、恢复与备份删除的审计记录。"""
        return await invoke(library_cleanup.audit_entries, app.state.cleanup_store)

    @mcp.tool(annotations=readonly, structured_output=False)
    async def component_get_references() -> CallToolResult:
        """读取本机草稿/发布快照的共享引用登记状态。"""
        return await invoke(app.state.reference_registry.status)

    @mcp.tool(annotations=write, structured_output=False)
    async def component_sync_references() -> CallToolResult:
        """立即同步共享引用登记，使清理流程能识别当前草稿与快照所用的发布版本。"""
        return await invoke(app.state.reference_registry.sync, force=True)
