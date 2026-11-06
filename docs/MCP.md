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

工具提供中文说明和 JSON 输入 schema，返回文本及 `structuredContent`。读取和修改工具有相应的只读/破坏性提示。删除成员标记为破坏性操作。

| 工具 | 功能 |
| --- | --- |
| `component_get_status` | 查看本服务和未接入能力，不请求上游 |
| `component_search_templates` | 按名称/编码搜索发布版本；`query`、`limit`、`offset` |
| `component_get_template` | 按 `publicationId` 读取发布元数据，可用 `sha256` 固定版本 |
| `component_get_template_preview` | 必须提供 `publicationId`、`sha256`，校验包和 STL，返回参数、原始接口和绝对模型地址 |
| `component_create_draft` | 新建持久化草稿；可设 `name`（省略或空字符串使用默认/文档名称），也可提供符合工具 schema 的内部 `ComponentDocument` |
| `component_list_drafts` | 列出草稿编号、名称、修订号和更新时间 |
| `component_get_draft` | 读取草稿及基础验证结果 |
| `component_update_definition` | 通过 `patch: {name?, code?, category?, description?}` 修改指定定义字段，未提供的字段保留 |
| `component_set_parameter` | 修改已有组件参数，`null` 表示未填写 |
| `component_update_rule` | 修改已有规则的说明文字，不执行表达式 |
| `component_update_member` | 通过 `patch: {role?, position?, rotation?, parameterValues?}` 修改已有成员，不重建几何 |
| `component_remove_member` | 删除成员，清理连接、继承接口、绑定，并清空槽位占用 |
| `component_add_mate` | 用明确的成员/接口标识记录贴合、同轴或重合关系，不进行求解 |
| `component_inherit_interface` | 将已有成员接口继承为组件对外接口 |
| `component_validate_draft` | 返回对应修订号的 `pass / fail / pending` 检查 |
| `component_export_draft` | 返回兼容 `component-1.0` 的 JSON，不代表正式发布 |

发布库搜索直接扫描配置的 MinIO 读取目录内全部零部件发布记录，`coverage` 返回 `source: "minio"`、实际 `fetched` 和 `completeLibrary: true`，完整范围指当前账号可读的配置目录。旧 HTTP 模式仍仅覆盖最近最多 **1000** 条，返回 `source: "legacy-http"`、`upstreamLimit: 1000` 和 `completeLibrary: false`。`count` 是当前来源范围内的匹配数。模型地址可供客户端另行下载；工具不会返回大型 STL 文本，也不会直接操控网页相机。

工程文档的内部结构是 `definition / assembly / ports / ...`，导出的兼容格式则是 `name / stages / ...`。两者用途不同；`component_create_draft(document=...)` 接收前者，不能直接传入导出文件。导入只保存已有工程数据，不会下载并转换零部件为装配成员。

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

参数值可以暂时超出范围并保存，基础验证会显示失败，便于保留编辑中的草稿。空草稿缺少成员和对外接口会失败。装配求解及规则执行始终为 `pending`，不会被计为通过。

## 网页同步

网页自动打开上次使用的草稿；没有草稿时创建一份。右侧“共享草稿”可选择 AI 创建的草稿，并复制包含 `?draft=<编号>` 的链接。当前编号保存在浏览器本地，实际草稿内容存于后端 SQLite，因此刷新页面和重启后端不会丢失已保存内容。

网页可见时每两秒读取最新状态，窗口重新聚焦时也会读取。编辑期间立即显示本地输入、串行保存；未保存操作不会被轮询覆盖。同步检查成功后显示后端验证结果；保存期间显示的是本地预检查。

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
