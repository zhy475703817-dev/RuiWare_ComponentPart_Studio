# MCP 接入与共享草稿

组件平台通过官方 MCP Python SDK 提供 **Streamable HTTP** 服务，与现有 FastAPI 同进程运行。启动方式不变，地址为 `http://127.0.0.1:8020/mcp`（不带结尾斜杠）。MCP 不经过 Vite；连接客户端时使用后端 8020 端口。

首次更新后，安装依赖并重启后端，或重新运行 `start-component.bat`。后端启动时初始化 SQLite 和 MCP 生命周期，不需要另起 MCP 进程。`GET /api/health` 只检查组件后端，不代表上游发布库可用。`GET /mcp` 不是网页或健康检查，应由 MCP 客户端初始化协议后使用。

## 连接

在支持远程 HTTP MCP 的客户端中填写服务器 URL。支持 `mcpServers` JSON 配置的客户端通常可以使用下列配置；具体字段以该客户端为准：

```json
{
  "mcpServers": {
    "ruiware-component": {
      "url": "http://127.0.0.1:8020/mcp"
    }
  }
}
```

未配置访问令牌时，MCP 只接受回环地址连接；同时验证 Host/Origin 防止 DNS 重绑定。设置独立的令牌后，客户端需发送 `Authorization: Bearer <令牌>`。这是预配置的 Bearer 令牌，不是 OAuth 登录服务：

```json
{
  "mcpServers": {
    "ruiware-component": {
      "url": "http://127.0.0.1:8020/mcp",
      "headers": { "Authorization": "Bearer <COMPONENT_MCP_TOKEN 的值>" }
    }
  }
}
```

`RUIWARE_PUBLIC_API_KEY` 是组件后端访问零部件平台的凭据，**不应填入 MCP 客户端**。MCP 访问令牌和上游密钥各自独立。

## 配置

在根目录 `.env.local` 或系统环境变量设置，重启后生效；系统环境变量优先。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `COMPONENT_DRAFT_DB` | `.runtime/components.sqlite3` | SQLite 草稿文件；备份此文件可保存草稿 |
| `COMPONENT_MCP_TOKEN` | 空 | MCP 入站访问令牌；为空时仅接受本机回环连接 |
| `COMPONENT_PUBLIC_BASE_URL` | `http://127.0.0.1:8020` | MCP 返回模型 URL 的服务来源，同时加入 MCP Host/Origin 允许列表；只填写来源，不带路径 |

发布库查询与预览复用组件后端的 `RUIWARE_MINIO_*` 连接，直接读取共享 bucket，无需启动零部件平台。`RUIWARE_TEMPLATE_API_URL`、`RUIWARE_PUBLIC_API_KEY` 仅在未配置 MinIO 读取连接时用于旧 HTTP 兼容模式；这些凭据均不填入 MCP 客户端。

远程部署需要设置实际 HTTPS 来源和 MCP 令牌，并在反向代理对**网页及所有 REST 接口**配置访问控制。MCP 令牌仅保护 `/mcp`，本版没有用户账户或按用户隔离草稿。不要把同一后端的草稿接口当成多租户服务。

## 工具

当前注册 **59 个工具**，提供中文说明和 JSON 输入 schema，返回文本及 `structuredContent`。读取和修改工具有相应的只读/破坏性提示。删除、批量动作和包含自动清理的发布库同步标记为破坏性操作。草稿编辑携带 `expectedRevision`；后台任务、发布库维护等按各自 schema 调用。

| 工具 | 功能 |
| --- | --- |
| `component_get_status` | 查看本服务和未接入能力，不请求上游 |
| `component_search_templates` | 按名称/编码搜索发布版本；`query`、`limit`、`offset`，`templateKind` 为 `part`（默认）、`component` 或 `all` |
| `component_get_template` | 按 `publicationId` 读取发布元数据，可用 `sha256` 固定版本 |
| `component_get_template_preview` | 必须提供 `publicationId`、`sha256`，校验包和 STL，返回参数、原始接口和绝对模型地址 |
| `component_create_draft` | 新建持久化草稿；未提供组件编码时自动分配唯一 `CMP-NNNN` 编码；可设 `name`（省略或空字符串使用默认/文档名称），也可提供符合工具 schema 的内部 `ComponentDocument` |
| `component_list_drafts` | 列出草稿编号、名称、修订号和更新时间 |
| `component_get_draft` | 读取草稿及基础验证结果 |
| `component_update_definition` | 修改名称、编码、类型、说明、项目编码、负责人、组织、标签、连接形式和设计约束等定义字段，未提供的字段保留 |
| `component_set_parameter` | 修改已有组件参数，`null` 表示未填写；按参数及绑定成员的有效范围约束数值，几何模式部分尺寸必须走联动 |
| `component_update_rule` | 修改已有规则的说明文字，不执行表达式 |
| `component_update_member` | 修改已有成员角色、位置、旋转、平面移动、参数覆盖和接口数据，不触发 CAD 试算 |
| `component_remove_member` | 删除成员，清理连接、继承接口、绑定，并清空槽位占用 |
| `component_add_mate` | 用明确的成员/接口标识记录贴合、同轴或重合关系，不进行求解 |
| `component_inherit_interface` | 将已有成员接口继承为组件对外接口 |
| `component_validate_draft` | 返回对应修订号的 `pass / fail / pending` 检查 |
| `component_export_draft` | 返回 `component-1.0` 或几何模式的 `component-1.1` JSON，不代表正式发布 |
| `component_apply_actions` | 按完整动作 schema 原子执行 1～100 个动作；成功只增加一次修订，失败全部回滚 |
| `component_add_member` | 将已有的完整成员数据加入草稿 |
| `component_add_template_member` | 用 `publicationId + sha256` 从发布库创建零部件/子组件成员；读取参数、真实接口和类型目录，省略位置时按实际 CAD 自动放置 |
| `component_copy_member` | 复制为独立实例并沿 X 平移 40 mm，保留原参数，不创建自动绑定或复制外部连接 |
| `component_ground_member` | 按旋转后的真实 CAD 最低点贴地；已有连接组整体平移 |
| `component_connect_interfaces` | 校验配合契约及占用，按真实 CAD 接口中心和法向自动对齐、记录连接；目标连接组整体变换并原子保存 |
| `component_remove_mate` | 删除连接，保留接口和成员位置 |
| `component_add_slot` | 新增空槽位，可指定基准接口 |
| `component_update_slot` | 修改槽位名称、必填性、基准和占用 |
| `component_remove_slot` | 删除槽位，保留装配成员 |
| `component_rename_interface` | 重命名组件对外接口 |
| `component_remove_interface` | 取消对外接口，保留成员原始接口 |
| `component_set_interface_type` | 设置或清空成员接口类型，可同时声明自定义类型和允许连接类型 |
| `component_set_interface_connectable_types` | 设置成员接口允许连接的类型列表 |
| `component_merge_interface_types` | 合并自定义接口类型目录，冲突时拒绝覆盖 |
| `component_add_parameter` | 新增组件参数定义及初始值 |
| `component_remove_parameter` | 删除参数及关联绑定、阵列 |
| `component_set_binding` | 设置成员参数绑定；`null` 移除 |
| `component_add_pattern` | 新增成员阵列，复用数量和间距参数 |
| `component_remove_pattern` | 删除阵列定义，保留原成员 |
| `component_add_rule` | 新增规则说明，不执行表达式 |
| `component_remove_rule` | 删除规则说明 |
| `component_resize_preview` | 启动真实 CAD 读取/试算，返回任务编号，不修改草稿 |
| `component_resize_status` | 读取任务状态、候选方案、预览与校验值 |
| `component_resize_cancel` | 取消试算，保留当前草稿 |
| `component_resize_apply` | 明确确认后用修订、任务号及 checksum 原子应用试算 |
| `component_resize_undo` | 恢复上次应用前状态，后来有编辑时拒绝覆盖 |
| `component_get_resize_history` | 读取当前可撤销任务的编号、checksum 和修订，后端重启后仍可用 |
| `component_list_publications` | 列出共享组件发布版本 |
| `component_get_publication` | 读取已发布组件文档、检查及绝对包下载地址，可校验 sha256 |
| `component_publish_draft` | 按草稿修订正式发布到共享存储，返回独立发布版本及下载地址 |
| `component_get_library` | 只读缓存列表、同步进度及错误，不触发同步或清理 |
| `component_sync_library` | 启动与网页相同的后台同步，包含符合条件的旧包自动备份清理 |
| `component_apply_library` | 将后台准备好的待应用列表切换为活动列表 |
| `component_preview_library_cleanup` | 扫描清理候选、占用与引用，保存 24 小时有效的 scanId，不删除包 |
| `component_cleanup_library` | 按 scanId 和已确认 publicationIds 复核、备份并清理，保留全部原有保护 |
| `component_list_backups` | 列出清理备份及到期时间 |
| `component_get_backup` | 校验备份并返回元数据、SHA-256 和绝对下载地址 |
| `component_restore_backup` | 恢复清理备份，拒绝覆盖不同内容 |
| `component_delete_backup` | 删除已到期备份，未到期时拒绝 |
| `component_get_cleanup_audit` | 读取清理、恢复和备份删除的审计记录 |
| `component_get_references` | 读取本机共享引用登记状态 |
| `component_sync_references` | 立即同步草稿与发布快照的共享引用登记 |

发布库搜索按 `templateKind` 扫描配置的 MinIO 读取目录，`coverage` 返回 `source: "minio"`、实际 `fetched` 和 `completeLibrary: true`，完整范围指当前账号可读的配置目录及发布列表规则。旧 HTTP 模式仅支持零部件，仍覆盖最近最多 **1000** 条，返回 `source: "legacy-http"`、`upstreamLimit: 1000` 和 `completeLibrary: false`；组件和全部搜索需要 MinIO。`count` 是当前来源范围内的匹配数。模型及包地址可供客户端另行下载；工具不会返回大型 STL 文本，也不会直接操控网页相机。

工程文档的内部结构是 `definition / assembly / ports / ...`，导出的兼容格式则是 `name / stages / ...`。两者用途不同；`component_create_draft(document=...)` 接收前者，不能直接传入导出文件。导入只保存已有工程数据；需要从发布库转换成员时，使用 `component_add_template_member`。

`component_add_mate` 只记录关系；`component_connect_interfaces` 才进行真实接口自动对齐。后者要求两端都有明确 CAD 定位来源；缺失、歧义或已在同一个连接组时返回错误，不按接口名称或显示标记猜测几何。几何试算的多解需先选择方案，再以返回的 `jobId + checksum + expectedRevision` 显式应用。试算和装配计算期间有其他编辑时，过期结果不会覆盖草稿。

正式发布与 JSON 导出独立。发布后可用组件发布工具立即读取；网页发布库沿用后台同步和“更新列表”流程。`component_sync_library` 可能自动清理旧包，且相应工具标记为破坏性；仅查询状态请使用 `component_get_library`。清理、恢复、备份过期限制、共享引用复核及审计均复用 REST 原流程；HTTP 错误和部分清理失败会返回 `isError: true` 及结构化错误结果。

## 使用示例

1. 调用 `component_list_drafts`，通过编号找到网页正在编辑的草稿；也可用 `component_create_draft` 新建。
2. 调用 `component_get_draft` 取得 `document.revision`。
3. 调用修改工具并携带当前修订号：

```json
{
  "name": "component_set_parameter",
  "arguments": {
    "draftId": "从草稿列表取得的编号",
    "expectedRevision": 1,
    "parameterId": "componentLength",
    "value": 3200
  }
}
```

4. 成功响应包含同一个 `draftId`、更新后的 `document` 和 `checks`；下次写入使用返回的新修订号。
5. 调用 `component_validate_draft` 检查；调用 `component_export_draft` 导出。

普通数值参数按当前参数范围及绑定成员的有效范围约束，整数绑定按整数处理。几何模式下受约束的尺寸修改会要求使用几何联动。空草稿缺少成员和对外接口会失败。几何联动检查仅在真实 CAD 试算并应用验证结果后通过，其他情况下为 `pending`；规则执行仍为 `pending`。

## 网页同步

网页自动打开链接 `?draft=` 指定的草稿或本机上次使用的草稿；没有草稿时创建一份。草稿编号保存在 URL 与浏览器本地，实际内容存于后端 SQLite。定义阶段可选择组件；Agent 通过 MCP 的 `component_list_drafts`、`component_get_draft` 和修改工具定位并编辑草稿，网页自动同步当前草稿。刷新页面和重启后端不会丢失已保存内容。

网页可见时每两秒读取最新状态，窗口重新聚焦时也会读取。编辑期间立即显示本地输入、串行保存；未保存操作不会被轮询覆盖。同步检查成功后显示后端验证结果；保存期间显示的是本地预检查。

普通编辑、批量编辑、装配操作及几何应用/撤销均在同一事务记录 `actor: "agent"` 和字段差异；REST 网页编辑保持 `actor: "user"`。批量编辑与自动对齐每次只创建一个修订，失败不留下部分连接或变换。来源由服务入口指定，客户端不能伪造。

网页手动编辑优先。Agent 先保存而网页仍基于旧修订提交时，后端仅在全部中间修订均有完整记录且均来自 Agent 时，将网页提交的动作应用于最新草稿：同一字段以用户值为准，其他字段保留 Agent 修改。用户先保存时，过期的 MCP 写入仍返回 `REVISION_CONFLICT`，`isError` 为 true，`structuredContent.error.current` 携带最新草稿。Agent 不得自动换用最新修订覆盖用户输入；重新读取后应核对并由用户决定。用户优先针对并发旧修订编辑，不会永久锁定用户曾改过的字段。

网页空闲时自动同步并显示“Agent 修改已同步”，差异面板按修订展示时间、中文字段名以及修改前后的值。保存响应也携带中间 Agent 修改，避免用户正在保存时漏掉差异提示；已发生用户优先合并时面板会明确说明。面板保留到用户关闭或切换草稿，最近最多展示 50 条修订变更，超出时显示提示。其他网页的修改不会标为 Agent。变更记录保存在 SQLite，升级前的历史不能追溯生成。

其他网页用户冲突、历史不完整、过期的重置动作仍返回 `REVISION_CONFLICT`。Agent 删除了用户正在编辑的对象或其引用时返回 `EDIT_TARGET_REMOVED`；其他结构冲突返回 `EDIT_STRUCTURE_CONFLICT`。这些情况不会假装保存成功，网页保留本页输入，暂停保存，提供“下载本地未保存副本”和“放弃未保存内容并重新读取”。本地副本是完整 `ComponentDocument`，可通过创建草稿工具的 `document` 参数恢复；导出工作流不将未保存副本当成共享草稿。网络失败可重试，但仍携带原修订号，防止响应丢失后重复覆盖。导出等待待保存队列完成；保存失败不会导出旧版本。关闭页面前若仍有未保存内容，浏览器会提示。

## REST 与检查

同一业务服务也提供：

```text
GET/POST /api/component-drafts
GET      /api/component-drafts/{draftId}
POST     /api/component-drafts/{draftId}/actions
GET      /api/component-drafts/{draftId}/validation
GET      /api/component-drafts/{draftId}/export
```

编辑请求为 `{ "expectedRevision": 1, "action": { "type": "definition.patch", "patch": { "name": "新名称" } } }`。完整模型和动作 schema 见 `/docs`。

`GET /api/component-drafts/{id}?afterRevision=5` 返回当前文档及 R5 之后最近最多 50 条 `changes`，`changesTruncated` 表示较早的变更被省略。每条记录包含 `actor`（`user`/`agent`）、`fromRevision`、`revision`、`createdAt` 和 `items`（`path`、`label`、`kind`、`before`、`after`）。来源由 REST/MCP 服务入口决定，不接受请求自行声明。保存响应额外提供 `reconciled`，表示应用过用户优先规则。全部检查和记录在同一 SQLite 事务中执行。

运行 `npm test`、`npm run build` 和 `.venv\Scripts\python.exe -m unittest discover -s backend/tests -v`。测试覆盖真实 SQLite 并发、后端重建后的持久化、前后端兼容、网页保存竞态、MCP 初始化/发现/调用、令牌和上游错误，以及官方 SDK 客户端连接临时真实 HTTP 服务的双向共享草稿链路。所有写入测试使用临时数据库。
