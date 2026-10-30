# 组件模板平台代码架构

## 目录与依赖

```text
src/main.tsx                      应用挂载
src/app/App.tsx                   工作台组合、各阶段数据与回调连接
src/app/WorkspaceLayout.tsx       顶栏、七阶段导航、三列布局、状态栏
src/app/stages.ts                 稳定的阶段标识及中文名称
src/app/useComponentWorkspace.ts  草稿 reducer、界面选择、导出副作用
src/stages/                      七个实际编辑面板
src/domain/component.ts          工程数据模型与新草稿默认值
src/domain/componentReducer.ts   无副作用的编辑操作及关联清理
src/domain/validation.ts         基础检查与待接入检查
src/domain/serialization.ts      导出格式，不依赖页面状态
src/library/                     发布列表、预览请求、STL 校验解析及资源生命周期
src/viewport/ComponentViewport.tsx 真实 STL 展示、相机适配、网格和预览状态
src/styles/workspace.css         工作台样式
backend/app/config.py             后端环境配置，密钥仅留在 Python
backend/app/library.py            发布库响应模型、只读调用和 API 路由
backend/app/published_storage.py  共享 MinIO 发布记录、包校验与上传边界
backend/app/library_cache.py      SQLite 发布包、默认模型和活动/待应用列表快照
backend/app/library_sync.py       后台增量同步、模型准入及缓存列表 API
backend/app/preview.py            包下载校验、安全 ZIP 读取、模型缓存和预览路由
backend/app/main.py               FastAPI 入口、生命周期、健康检查、静态页面
backend/app/components/           共享工程模型、编辑/验证/导出、SQLite 修订事务、REST
backend/app/mcp/                  官方 MCP SDK Streamable HTTP 入口及业务工具
src/app/sharedDraft.ts            共享草稿请求、串行保存、乐观显示、轮询和冲突恢复
src/app/AgentChanges.tsx           Agent 来源提示、修订记录、中文字段前后差异
backend/app/components/changes.py  文档差异提取，列表按稳定 ID 对齐
backend/tests/test_api.py         后端接口及配置测试
backend/tests/test_preview.py     发布包及模型预览测试
scripts/start-dev.ps1             一键安装、后台启动及就绪检查
tests/                           Node 原生领域和前端列表测试
```

前端依赖方向：`app → stages / library / viewport / domain`，`stages → domain（类型）`，`viewport → domain / library（类型）`。`domain` 不导入 React、DOM、网络、服务端配置或 Three.js。后端 Python 模块不进入浏览器构建。

运行链路：`浏览器 → Vite :5180 /api 转发 → FastAPI :8020 → SQLite 快照`，后台直接从共享 MinIO 读取零部件和组件发布记录、按需下载并校验包，准备待应用的新列表。两类发布使用同一个 bucket，以 `templateKind` 区分，旧记录默认是零部件；无需启动零部件平台。`metadata.json` 是发布完成标记，包路径须与记录处于同一发布目录，包大小和 SHA-256 必须一致。读取仅需存储连接与读取目录，组件发布另外要求成员写入目录。

仅在未配置 MinIO 读取连接时，零部件回退到旧 HTTP 发布接口，保留最近 1000 条限制、超时和拒绝重定向行为。MinIO 连接故障不会切换来源，已有缓存保留。业务异常保留安全错误码，不返回 SDK 内部详情或密钥。配置来自根目录 `.env`、`.env.local` 和系统环境变量，系统环境变量优先。

FastAPI 提供 `/api/health`、`/api/published-templates`、兼容 STL 预览接口，以及 `/api/published-templates/{id}/reconstruction` 和 `/api/published-templates/{id}/reconstruction/model` 本地重建接口；健康检查不联系零部件平台。发布包重建先固定发布哈希并验证整包，再将白名单定义交给项目内固定版本 CAD 引擎；建模在受并发限制的子进程中完成，临时输出完成后清理。前端只收到 STL 和用户可调参数。开发与预览由 Vite 转发 `/api`，不开放跨域。构建完成后也可由 FastAPI 直接提供 `dist`，详情见 README 与 `PART-PREVIEW.md`。

## 七阶段

| 标识 | 面板 | 当前职责 |
| --- | --- | --- |
| definition | DefinitionStage | 编辑名称、编码、类型、设计说明 |
| assembly | AssemblyStage | 记录接口连接和继承接口；显示实际能力边界 |
| interfaces | InterfacesStage | 显示对外接口来源和槽位占用 |
| parameters | ParametersStage | 编辑数值，显示范围与绑定数据 |
| rules | RulesStage | 编辑规则说明 |
| validation | ValidationStage | 显示当前草稿修订对应的基础检查及待接入检查 |
| publish | PublishStage | 汇总检查结果并导出草稿 |

阶段只是编辑视角。切换阶段不修改工程数据，也不表示该阶段已完成。

## 三类状态

1. 工程文档：定义、成员、连接、接口、槽位、参数、绑定、规则。后端 `components` 服务是持久化编辑、验证、导出的权威实现，SQLite 事务检查 `expectedRevision` 并保存。前端 `componentReducer` 仅将待保存动作投影为即时输入显示，服务端结果确认后替换；跨语言测试检查兼容行为。有效编辑增加修订号，新建创建独立草稿。
2. 页面交互：当前阶段、所选成员、预览模板、状态消息在工作台 hook 中；搜索、列表加载和详情展开在 library 中。收起详情不会清除工作台预览选择，新建会清除工作台选择并收起发布详情，保留列表与搜索。
3. 运行时三维资源：预览 hook 管理请求取消、竞态保护和 geometry 释放，viewport 管理材质、相机、网格和轨道控制。切换及新建清理旧资源，自动对焦不改变模型原点和单位。渲染对象和预览结果不进入文档。

## 引用与数据操作

- 发布模板由 `publicationId + templateId + version + sha256` 固定引用。多个成员可引用同一版本，但拥有独立成员标识、位置、姿态和参数覆盖值。
- 连接和接口在内存中使用 `{ memberId, interfaceId }`。标识含点号也不会被错误拆分。
- 删除成员清理关联连接、对外接口和参数绑定；保留槽位定义，将其占用清空。必填空槽位会在验证中失败。
- 位置使用毫米、旋转使用弧度，工程坐标为右手 Z 向上。正式模型加载时，在 viewport 边界明确处理文件单位与坐标，不能修改工程定义来适配显示。
- reducer 支持新增/编辑/删除成员、新增连接和对外接口，以及删除成员时的关联清理；已接入发布模板本地模型预览，但预览选择仍不等于加入装配，也不会创建示例成员来代替真实零部件。

## 验证与导出

`validateComponent(document)` 每次对当前文档计算 `pass / fail / pending`。基础数据检查不能证明几何可装配或规则满足，装配求解和规则执行目前固定为 pending。

网页保存完成后的检查与导出使用后端 `validate_component` / `serialize_component`，保存期间使用前端纯函数显示预检查；两者受共享文档兼容测试约束。`sharedDraft` 控制器串行保存，空闲时读取 AI 修改并持续展示前后差异，保存响应中的 Agent 变更同样进入提示。SQLite 在同一事务记录来源和差异；用户旧修订写入只在全部中间修订均来自 Agent 时重放该动作，实现同字段用户优先、其他字段保留。MCP 仍使用精确修订检查；其他用户窗口冲突、历史不全、重置和对象删除不会自动合并。保存失败时保留本地投影，可下载本地副本，显式重新读取才放弃未保存内容。草稿选择保存在 URL `?draft=` 与 localStorage，工程数据保存在后端。

MCP `/mcp` 与 REST 共用 `DraftRepository`、HTTP 客户端和预览缓存，MCP 会话管理器由 FastAPI lifespan 启停。预览通过显式 `client/settings/cache` 参数复用，不依赖虚构的 HTTP Request。MCP 工具不会模拟浏览器点击，模型返回固定发布哈希的绝对下载 URL。详见 [MCP 接入](MCP.md)。

`serializeComponent(document)` 保留 `component-1.0` 的 `name`、`stages.definition`、`stages.assembly`、`stages.ports`、参数值、规则及验证主体结构。增加修订、完整版本引用、结构化引用、参数定义、槽位和绑定。旧连接字符串仍导出，新增读取方应使用 `fromRef/toRef` 以避免含点号标识的歧义。旧接口的 `sourceMemberId/sourceInterfaceId` 字段保留。

序列化入口只接收工程文档，显式生成输出，不导出页面选中状态、预览记录、密钥、相机或模型缓存。pending 检查的旧式 `passed` 字段为 false。导出不创建发布版本，不把草稿标为已发布。

## 下一步扩展位置

发布包完整性与兼容 STL 预览位于 `backend/app/preview.py`，本地重建位于 `backend/app/reconstruction.py`，固定 CAD 引擎快照位于 `backend/engine/`；前端 `library` 管理参数、请求取消和模型资源。下一步“加入装配”应映射已解析定义并明确发出 `member.add`；点击预览始终不修改工程文档。原始接口契约保留在发布定义中，不能用接口名称推断装配位置。

真实模型已接入 `viewport`。成员变换、接口求解、参数求值属于工程能力，应通过工作台协调和领域操作写回数据。正式发布另接发布服务，不用本地 JSON 下载代替发布。
