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
| `component_get_authoring_requirements` | 读取默认 AI 制作要求：在选型、装配、参数配置时预防接口、联动及尺寸问题 |
| `component_search_templates` | 按名称/编码搜索发布版本；`query`、`limit`、`offset` |
| `component_get_template` | 按 `publicationId` 读取发布元数据，可用 `sha256` 固定版本 |
| `component_get_template_preview` | 必须提供 `publicationId`、`sha256`，校验包和 STL，返回参数、原始接口和绝对模型地址 |
| `component_create_draft` | 新建持久化草稿；未提供组件编码时自动分配唯一 `CMP-NNNN` 编码；可设 `name`（省略或空字符串使用默认/文档名称），也可提供符合工具 schema 的内部 `ComponentDocument` |
| `component_list_drafts` | 列出草稿编号、名称、修订号和更新时间 |
| `component_get_draft` | 读取草稿及基础验证结果 |
| `component_update_definition` | 通过 `patch: {name?, code?, category?, description?}` 修改指定定义字段，未提供的字段保留 |
| `component_set_parameter` | 修改已有组件参数，`null` 表示未填写 |
| `component_update_rule` | 修改已有规则的说明文字，不执行表达式 |
| `component_update_member` | 通过 `patch: {role?, position?, rotation?, parameterValues?}` 修改已有成员，不重建几何 |
| `component_remove_member` | 删除成员，清理连接、继承接口、绑定，并清空槽位占用 |
| `component_add_mate` | 用明确的成员/接口标识记录贴合、同轴或重合关系，不进行求解 |
| `component_inherit_interface` | 将已有成员接口继承为组件对外接口 |
| `component_validate_draft` | 返回对应修订号的基础检查及 `authoring` 制作检查；未验证不算完成 |
| `component_configure_authoring` | 用当前 `expectedRevision` 声明 `config`：`axes`（null 自动，0/1/2 对应 XYZ）、成对的 `minimumDimensions` / `maximumDimensions`、最多 8 组 `targets`，保存后自动重验 |
| `component_finish_authoring` | 用当前 `expectedRevision` 启动/读取自动制作验收；失败后可设 `retry: true` 重试 |
| `component_authoring_status` | 轮询自动验收进度、测试点及失败原因；仅当前修订 `deliveryReady: true` 可称制作完成 |
| `component_resize_preview` | 真实 CAD 试算；`inspectOnly: true` 仅测量，默认 false 且省略 `dimensions` 可在制作中检查原尺寸联动 |
| `component_resize_status` | 读取试算结果、缺失关系和需要选择的候选 |
| `component_resize_apply` / `component_resize_undo` | 应用经确认的预览 / 按版本恢复应用前状态 |
| `component_resize_cancel` | 取消试验预览，保留原组件 |
| `component_export_draft` | 返回旧 `component-1.0` 或带几何设计的 `component-1.1` JSON；导出成功不表示联动或工程验收通过 |

发布库搜索直接扫描配置的 MinIO 读取目录内全部零部件发布记录，`coverage` 返回 `source: "minio"`、实际 `fetched` 和 `completeLibrary: true`，完整范围指当前账号可读的配置目录。旧 HTTP 模式仍仅覆盖最近最多 **1000** 条，返回 `source: "legacy-http"`、`upstreamLimit: 1000` 和 `completeLibrary: false`。`count` 是当前来源范围内的匹配数。模型地址可供客户端另行下载；工具不会返回大型 STL 文本，也不会直接操控网页相机。

工程文档的内部结构是 `definition / assembly / ports / ...`，导出的兼容格式则是 `name / stages / ...`。两者用途不同；`component_create_draft(document=...)` 接收前者，不能直接传入导出文件。导入只保存已有工程数据，不会下载并转换零部件为装配成员。

## 默认 AI 制作要求

平台在每次 MCP 初始化时自动发送制作要求，工具说明同时提示相应步骤；不需要用户每次重新粘贴提示词。已有客户端应重新连接以更新初始化说明和工具描述，也可直接调用 `component_get_authoring_requirements`。规则集中维护在 `backend/app/mcp/authoring.py`。

要求在制作过程中预防问题：选型先读取固定版本的真实接口及参数，装配时同步建立有依据的连接关系，检查子组件内部叶子和端口映射，并确认尺寸范围、整数/离散规格和锁定意图。不得虚构接口、放宽模板限制、随意连接或自动锁定报错零件来绕过检查。

创建、读取、编辑、几何应用/撤销及验证工具返回额外的 `authoring` 字段，包含策略版本、草稿修订、`issues`、`requiredNextStep` 和 `geometryVerified`。未确认关系、未读取子组件来源、缺少接口/参数资料会在制作时返回具体零件路径；超限、非整数、离散规格不符及基础数据失败会返回 `needs_attention`。这些结果不写入工程文档，不自动移动零件，不阻止中间草稿保存。

缺少显式关系只是需要处理的线索：真实 CAD 可能自动识别接触关系，不能仅凭元数据缺失判定结构不可实现。AI 应在装配中尽早调用完整原尺寸试算，然后在合法范围内分别试调用户要求支持方向的增大、缩小以及预期多轴组合；孔列数量变化时须保留连接、对外端口及槽位引用的接口与来源。可确定的问题就地修复并复验，不能唯一判断的设计取舍交由用户确认。`inspectOnly`、文字说明和自行导入的 `verified: true` 不算联动验收。`authoring.geometryVerified` 只认可本服务器实际应用过、修订与校验值一致的结果；继续编辑后需要重新验证。

保存编辑后，平台自动执行原尺寸完整 CAD 检查、增大/缩小/组合试调、部分孔列边界抽查和复验。默认范围为识别方向的 ±10%；应通过 `component_configure_authoring` 设置用户实际要求。明确范围中的变化方向即使未列入 `axes` 也必须测试，额外测试点必须位于明确范围内。每个目标从原尺寸副本独立试算；几何求解失败时尝试一次中间尺寸续算，始终保留锁定、连接和来源限制。所有测试通过后原尺寸结果才以 Agent 修订原子保存，测试尺寸不覆盖交付尺寸。不能确定的关系会返回 `needs_input`，不能实现的目标保留失败，不自动缩小范围。

`authoring` 还包含 `qualification` 和 `deliveryReady`。验收证据绑定文档修订、内容、范围和策略版本；编辑后自动失效重验。发布 API 强制要求有效证据，旧快照和导入标志不能绕过。重启后继续未完成任务。当前机制不调用外部 AI；自动修复限于明确约束内的几何求解，不会自行替换零件或猜测设计意图。有限测试点通过不代表区间内任意尺寸、承载或焊缝等工程要求通过。

每个柱片模板默认必须内置撑杆补齐能力。AI 在制作阶段读取真实孔列和撑杆端口、保存规则并绑定高度，缺规则、遗漏成员或缺少高度绑定时不能通过制作验收与发布。用户使用时只需选择是否自动补齐与目标高度，无需再要求 AI 配置。关闭使用开关不能免除原尺寸、增高与缩短的补齐能力验收。

柱片模板的自然语言需求示例：

```text
制作柱片模板。修改柱片高度时，横撑和斜撑保持原长度、截面和规格，不允许拉伸；高度增加时按新增孔位自动补齐，高度减少时删除不再需要的撑杆；所有撑杆端口必须连接到真实孔位，按指定孔距和余量策略处理。
```

AI 应据此读取真实孔位和撑杆端口，调用 `component_set_brace_generation_rule` 保存规则，并在制作阶段验证增高、缩小和重复试算；用户不需要每次重复粘贴实现细节。仅要求孔位几何定位时明确设置 `connectionMode: "geometry"`，平台校验真实孔心对准及交替斜撑方向；需要正式装配连接时设置 `connectionMode: "mates"` 并检查契约及容量，旧规则默认采用该模式。`component_get_authoring_requirements` 的 `braceConnectionModes` 可重新读取两种用途，缺少契约的正式撑杆连接会在制作检查中提前提示。

多条规则可共用立柱，但横撑、斜撑原型各自只能由一条规则使用。需要同规格原型时新增独立成员并引用同一发布版本，不能复用一个成员或引用规则生成实例。孔列模式须含一个 `{index}` 或 `{index:03d}`，横撑及斜撑孔距均引用正数 mm 参数。写入冲突时 `error.code` 保持稳定，相关错误还返回中文 `message` 和包含规则、成员的 `details`，写入失败不改变草稿。

| 常见失败码 | 制作时处理 |
| --- | --- |
| `BRACE_RULE_PROTOTYPE_CONFLICT` | 为另一条规则添加独立撑杆原型成员，可引用同一发布模板 |
| `BRACE_RULE_MEMBER_ROLE_CONFLICT` | 将立柱和撑杆分成不同成员 |
| `BRACE_RULE_GENERATED_PROTOTYPE` | 用独立原型替换会被重建清理的生成实例 |
| `INVALID_BRACE_RULE_PATTERN` / `BRACE_RULE_PATTERN_INVALID` | 根据真实孔列 ID 填写含单个编号占位符的模式 |
| `INVALID_BRACE_RULE_UNIT` / `BRACE_RULE_UNIT_INVALID` | 高度、余量及横撑、斜撑孔距均使用 mm 参数 |
| `BRACE_RULE_PARAMETER_RANGE` | 调整目标或选型，不覆盖来源模板限制 |
| `BRACE_RULE_NODE_CONFLICT` | 检查是否用相同来源、规格和端口对应关系重复生成同一根撑杆 |

完整分格规则的默认高度试调会选取合法分格尺寸，而非直接使用 ±10% 后的非整格高度；明确填写的范围端点和目标保持原值，不自动改写。

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
5. 调用 `component_validate_draft` 检查，按实际需求配置范围；调用 `component_finish_authoring`，轮询 `component_authoring_status` 到终态。只有 `deliveryReady: true` 才标记制作完成；自动保存可能增加修订，下次写入前重新读取草稿。`component_export_draft` 可导出未完成草稿，不等于完成验收。

参数值可以暂时超出范围并保存，基础验证会显示失败，便于保留编辑中的草稿。空草稿缺少成员和对外接口会失败。几何联动通过真实试算和应用验证；文字工程规则执行仍为 `pending`，不会被计为通过。AI 应同时处理 `authoring` 制作检查，并遵循上述默认制作要求。

## 网页同步

柱片规则受几何锁定约束：`RESIZE_LOCK_CONFLICT` 表示新高度与已锁定的立柱长度或撑杆实例冲突。AI 应说明具体冲突，按用户设计选择修改高度或锁定，不能自行解除锁定。试算结果的 `changes` 记录已有成员调整，`structureChanges.added/removed` 记录新增、移除成员，应用前应核对这两部分。

网页自动打开本机上次使用的草稿；没有草稿时创建一份。草稿编号保存在浏览器本地，实际内容存于后端 SQLite。网页不提供草稿分享链接或草稿切换面板；Agent 通过 MCP 的 `component_list_drafts`、`component_get_draft` 和修改工具定位并编辑草稿，网页自动同步更新。刷新页面和重启后端不会丢失已保存内容。

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
GET/POST/DELETE /api/component-drafts/{draftId}/qualification
PUT      /api/component-drafts/{draftId}/qualification/config
```

编辑请求为 `{ "expectedRevision": 1, "action": { "type": "definition.patch", "patch": { "name": "新名称" } } }`。完整模型和动作 schema 见 `/docs`。

`GET /api/component-drafts/{id}?afterRevision=5` 返回当前文档及 R5 之后最近最多 50 条 `changes`，`changesTruncated` 表示较早的变更被省略。每条记录包含 `actor`（`user`/`agent`）、`fromRevision`、`revision`、`createdAt` 和 `items`（`path`、`label`、`kind`、`before`、`after`）。来源由 REST/MCP 服务入口决定，不接受请求自行声明。保存响应额外提供 `reconciled`，表示应用过用户优先规则。全部检查和记录在同一 SQLite 事务中执行。

运行 `npm test`、`npm run build` 和 `.venv\Scripts\python.exe -m unittest discover -s backend/tests -v`。测试覆盖真实 SQLite 并发、后端重建后的持久化、前后端兼容、网页保存竞态、MCP 初始化/发现/调用、令牌和上游错误，以及官方 SDK 客户端连接临时真实 HTTP 服务的双向共享草稿链路。所有写入测试使用临时数据库。
