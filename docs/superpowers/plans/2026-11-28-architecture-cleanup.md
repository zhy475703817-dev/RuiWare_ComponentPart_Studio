# 框架与代码简洁性整改方案

> **For agentic workers:** 使用 executing-plans 按任务顺序实施。下方复选框用于记录完成情况；本文件只提出方案，未实施代码修改。

**Goal:** 修复已复现的数据与事务问题，收拢重复规则、资源加载和模块职责。

**Architecture:** 保留 React + FastAPI + SQLite + 固定 CAD 引擎。前端负责交互与乐观显示，后端负责编辑事务、校验、发布；REST 与 MCP 调用共同业务函数。先完成可观察行为修复，再做保持行为的职责整理。

**Tech Stack:** TypeScript、React、Node 原生测试、Python、FastAPI、Pydantic、SQLite、unittest、Three.js、NumPy/SciPy、OCCT。

**Spec:** 本会话的框架审查及用户“给出具体修改方案”的请求；已复现的附件导出失败、连接部分保存、整数舍入差异是前三项验收依据。

## 约束与批次

- 第 1 批：任务 1–3，修复可复现问题；第 2 批：任务 4–5，统一加载与服务边界；第 3 批：任务 6–8，整理职责并清理残留。
- 每个任务单独形成可审查的改动；功能修复与纯结构迁移分别提交。
- 不增加第三方依赖；保留现有单动作 REST 调用和 MCP 工具的兼容性。
- 保留修订检查、来源与差异记录、用户优先合并、附件限制、固定版本校验、引用保护、清理备份及制作验收门槛。
- 存储格式暂不删字段；已有草稿、发布包和离线缓存继续可读。
- 测试针对业务结果；源码搬移不编写逐函数镜像测试。
- 文件长度只作线索，不作为拆分目标；不以行数减少评价是否完成。

## 任务 1：统一附件来源，修复导出与恢复副本

**修改：** `src/app/useComponentWorkspace.ts`、`src/stages/DefinitionStage.tsx`、`backend/app/components/repository.py`。
**新增：** `src/library/draftEvidence.ts`、`src/app/documentExport.ts`、`tests/document-export.test.mjs`。
**测试：** 扩展 `backend/tests/test_components.py`。
**删除：** 完成调用迁移后删除 `src/domain/evidenceStore.ts`。

- [x] 先加入回归用例：服务端存在附件、浏览器没有 IndexedDB 数据，正常导出仍含完整字节；本地副本保留未保存名称及附件选择；附件读取失败不生成声称完整的下载。
- [x] 正常导出直接下载 `controller.export()` 的结果，使用响应中的名称生成文件名，不再调用旧 `exportEvidenceFiles`。
- [x] 将附件上传和读取移到 `draftEvidence.ts`，表单只负责文件选择与结果展示；保持 5 MB/10 个/15 MB 限制。
- [x] 本地副本在点击时捕获 `draftId + structuredClone(document)`；以该副本的 `definition.evidenceFiles` 为附件清单，已有 `dataUrl` 直接保留，否则从捕获的草稿读取。读取后更新副本内对应文件的 `dataUrl`。
- [x] 本地副本不调用保存、reload 或 flush，避免覆盖尚未保存内容；若引用文件已被其他操作移除，明确提示具体文件无法读取。
- [x] 后端 `export` 在同一个 SQLite 读事务中读取文档与关联文件，复用现有 `_portable_evidence` 校验大小和哈希，避免导出期间发生修订变化而混用附件。
- [x] 删除旧 IndexedDB 调用链；`evidenceFiles` 为文件身份和内容清单，`evidence` 暂保留为旧显示元数据，不再承担独立文件存储。

建议边界（新增函数名）：

```ts
// draftEvidence.ts：远端文件访问，返回现有 EvidenceFile 类型。
uploadDraftEvidence(draftId: string, id: string, file: File): Promise<EvidenceFile>
readDraftEvidence(draftId: string, file: EvidenceFile): Promise<EvidenceFile>

// documentExport.ts：在传入副本中嵌入附件，不读取 React 状态。
embedDraftEvidence(
  document: ComponentDocument,
  load: (file: EvidenceFile) => Promise<EvidenceFile>,
): Promise<ComponentDocument>
```

**验收：** 上传→保存→导出→后端重新导入能恢复同一文件；未保存副本保留本地修改且可恢复附件；无附件路径仍正常。

## 任务 2：让复合编辑以一个事务保存，并返回本次操作结果

**修改：** `backend/app/components/models.py`、`router.py`，`src/app/sharedDraft.ts`、`useComponentWorkspace.ts`。
**测试：** `tests/shared-draft.test.mjs`、`backend/tests/test_components.py`、`backend/tests/test_collaboration.py`。

- [x] 保留原 `POST /{draft_id}/actions` 的单动作语义；增加 `POST /{draft_id}/actions/batch`。请求模型 `ActionBatchRequest` 包含 `expectedRevision` 和长度 1–100 的 `actions: list[TypedAction]`。
- [x] 批量路由直接调用现有 `DraftRepository.update(..., actions, actor="user")`。一次成功只增加一个修订，任意动作失败全部回滚。
- [x] `SharedDraftController` 的队列项由单动作改为事务单元；原 `dispatch(action)` 包装成单动作队列项，保留现有调用方式。
- [x] 新增 `commit(actions): Promise<DraftEnvelope>`，只有该队列项收到后端确认才 resolve；业务拒绝、修订冲突、网络错误、切换或 dispose 均使本次等待者得到明确失败。网络错误和冲突仍保留待处理输入，继续使用原 retry/reload 恢复行为；操作 Promise 失败不等于删除本地输入。
- [x] 投影按队列项逐组计算。组内先应用全部动作，再统一修订号：有变化时只增加一次，与后端一致；任一步不能重放时，不投影部分组。
- [x] 后端拒绝一个批次后移除整组，再从已确认文档重放后续输入；后续动作引用已被拒绝的新增对象时进入现有冲突恢复，不能静默丢弃。
- [x] `connectInterfaces` 一次提交 `[mate.add, member.patch]`，等待 `commit` 后才显示成功；不再用 `flush` 推断本次连接是否成功。
- [x] 加入成员并自动放置时，将接口类型合并、成员加入和位姿变更按一次操作提交。先在副本完成类型合并和尺寸测量；检查当前草稿/修订后再排队，避免失败留下半成品。
- [x] 撤销快照以复合操作为单位；失败操作不留下虚假撤销记录。

建议接口：

```ts
// DraftApi 新增；原 apply 保持兼容。
applyBatch(id: string, revision: number, actions: ComponentAction[]): Promise<DraftEnvelope>

// SharedDraftController 新增。
commit(actions: ComponentAction[]): Promise<DraftEnvelope>
```

**必须通过的用例：**

| 输入/故障 | 结果 |
| --- | --- |
| 连接与移动均合法 | 两者保存，修订 +1 |
| 连接失败、移动合法 | 两者均不保存，commit reject，无成功提示 |
| 连接合法、移动非法 | 两者均不保存 |
| 正确操作之后还有快速输入 | 按顺序保存，输入不丢失 |
| 网络失败或修订冲突 | 完整本地输入保留，能够重试或下载 |
| 保存中切换/卸载 | 原操作不修改另一草稿、不显示另一草稿的成功提示 |

## 任务 3：固定跨语言数值与动作契约

**修改：** `backend/app/components/service.py`。
**新增：** `tests/fixtures/component-actions-v1.json`。
**测试：** `tests/component-domain.test.mjs`、`backend/tests/test_components.py`。

- [x] 先把已复现的 `member.add` 整数绑定 2.5 用例加入共享 fixture，确认当前两端结果不同。
- [x] 约定整数绑定舍入采用 `floor(value + 0.5)`，与当前前端 `Math.round` 一致；忽略 IEEE -0 的表示差异，数值均为 0。后端动作计算不再混用 Python 的银行家舍入。
- [x] 检查新增成员、显式参数修改、绑定建立和阵列数量推断中的相关舍入点；只统一组件编辑语义，不批量替换 CAD 几何中的所有 `round`。
- [x] 用同一初始文档和动作序列分别运行 TS reducer 与 Python apply_action，比较文档、修订号及有效范围。扩展现有跨语言测试入口，不新增测试框架。
- [x] fixture 覆盖 2.5、3.5、-2.5（适用于允许负值的整数参数）、null、上下限、绑定范围交集、成员删除的引用清理。

**验收：** 两端每个动作后的有效文档一致，保存确认时不会发生 3→2 跳变。旧数据不自动重写。

## 任务 4：统一固定版本 CAD 来源加载，解除模块依赖环

**修改：** `geometry_sources.py`、`resize_jobs.py`、`resize_solver.py`、`brace_generation.py`（均位于 `backend/app/components/`）。
**新增：** `backend/app/components/geometry_common.py`。
**测试：** `test_mcp_assembly.py`、`test_geometry_resize_api.py`、`test_resize_inspection_cache.py`、`test_brace_policy.py`。

- [x] 将现有 `ResizeFailure` 和 `fingerprint` 原样迁入 `geometry_common.py`；求解器、撑杆生成、来源加载直接从该模块导入。既有公开导入路径保留重导出兼容。
- [x] 以 `geometry_sources.load_sources` 为唯一来源遍历实现，参数扩展为 `load_sources(app, document, cancelled=..., *, resource=None)`；`resource` 的签名与现有 `ResizeJobs.resource(key, loader)` 相同。
- [x] 装配调用不带 resource；试算传入 `jobs.resource`，继续共享正在进行的加载与缓存。`load_geometry` 只组合来源和 CadProvider。
- [x] 模板身份 key 与哈希比较统一使用小写 SHA-256，不修改文档中的原始版本引用。
- [x] 保留 16 层上限、循环检测、取消、templateId/version/hash 核对、嵌套 instanceDocument 和零件来源错误详情。
- [x] 删除 resize_jobs 内重复的 collect 递归；异常类迁移完成后，brace_generation 不再依赖 resize_solver。

**验收：** 大小写哈希行为一致；同版本重复成员只加载一次；取消和缺失来源仍返回原约定错误；热缓存不增加 CAD 构建或下载次数。

## 任务 5：REST 与 MCP 共用业务函数

**修改：** `backend/app/library_cleanup.py`、`library_sync.py`、`reference_registry.py`、`reconstruction.py`、`components/resize_jobs.py`、`components/assembly.py`、`components/geometry_sources.py`、`components/qualification.py`，以及 MCP 对应工具文件。
**测试：** 现有 MCP、维护、同步、清理、预览及几何 API 测试。

- [x] 先处理维护入口：把 Request 解包限定在路由内。业务函数接受现有 state/服务对象和业务参数，返回普通 dict；REST 包装 JSONResponse，MCP 包装 CallToolResult。
- [x] 复用 `execute_cleanup`、`cleanup_preview`、`LibrarySync.start/snapshot` 和缓存 apply；不新建通用服务基类。
- [x] 清理中的“部分失败”仍向 REST 返回 409，MCP 返回 isError=true；不能因去掉 JSONResponse 而丢失状态语义。
- [x] 再处理 CAD 入口：`ResizeJobs.start` 从对象已有的 self.app 获取上下文，移除 request 参数；修改 REST、MCP 和验收调用者，删除 SimpleNamespace(app=app)。
- [x] 将 reconstruction 的模板读取业务函数改为显式接收 state；HTTP 路由在边界取 request.app.state，装配和来源加载传入同一 state，不再伪造 Request。
- [x] 保留 MCP 的 actor="agent"、REST 的 actor="user"，以及现有异常码、鉴权和原子修订。

**验收：** 相同操作在 REST 和 MCP 返回同一业务结果及错误；服务调用不需要创建 Request/Response；无重复清理、重复发布或 actor 丢失。

## 任务 6：收拢前端工作台与画布职责

**修改：** `src/app/useComponentWorkspace.ts`、`src/viewport/ComponentViewport.tsx`。
**新增：** `src/app/useModelEditing.ts`。
**测试：** 沿用 shared-draft、model-undo、assembly-groups、viewport-selection、camera-view 测试及浏览器交互检查。

- [x] 将撤销历史、复制/粘贴、分组/解组、成员变换与选择操作作为一个模型编辑单元迁入 useModelEditing；注入现有 controller ref、editable 和状态消息回调，避免每项能力各建一个 hook。
- [x] useComponentWorkspace 保留草稿生命周期、阶段、预览选择，以及对编辑/导出/发布能力的组合；App 对外回调保持稳定。
- [x] 快照和锁保持一个所有者。异步操作使用调用时捕获的草稿和文档，提交前核对；不新增第二份工程文档状态。
- [x] 画布先在原文件内明确 scene rendering、transform editing、tooltip 三组内部职责；只有能移出并减少实际参数传递时才提取独立文件，不新增全局 Context 来隐藏依赖。
- [x] 核对整个操作链：Ctrl+C/V、Ctrl+G、撤销 15 步、单选/多选、贴地、组件内部选择、切换草稿清理，均保持原有行为。

**验收：** 模型编辑状态只维护一份；已修复的批量事务语义持续适用；相机与 geometry 释放不受编辑拆分影响。

## 任务 7：整理后端动作分发、求解器和发布包组装

**修改：** `backend/app/components/service.py`、`resize_solver.py`、`repository.py`、`publications.py`。
**新增：** `backend/app/components/publication_package.py`。
**测试：** 组件动作、共享契约 fixture、几何求解、真实 CAD、制作验收及发布工作流测试。

- [x] `apply_action` 保留唯一公共入口；在同文件提取 `_apply_member_action(data, action)`、`_apply_interface_action(data, action)`、`_apply_parameter_action(data, action)` 三组明确职责。辅助函数只修改当前副本，最终验证、修订递增和 verified 失效统一在入口处理。
- [x] 创建默认文档、验证、序列化入口保持不动。避免为每个 action 创建类、插件注册器或额外工厂。
- [x] `ResizeSolver.solve` 先以原算法阶段提取私有方法：测量与约束准备、变量与离散规格准备、候选搜索及精确复核、结果组装；闭包变量显式传递，禁止借机更改容差、权重、求解次数和方案排序。
- [x] 提取后的 orchestration 必须仍执行原取消/超时检查、撑杆重建、精确 CAD 复核、接口保护和多解处理。保留 inspectOnly 快路径。
- [x] 发布包组装提为纯函数 `build_component_package(manifest, document, checks, qualification, evidence=None) -> bytes`。仓储在同一事务中取得输入并保存快照；远程上传继续由 publications 负责。
- [x] 保留原 ZipInfo 时间、压缩方式、JSON 排序和分隔符，保证相同输入的包字节稳定；原发布快照和哈希不重建。

**验收：** 共享动作 fixture 不变；固定 CAD 用例的尺寸、接口、结构变化与误差阈值不变；发布内容与固定输入包哈希不变；不减少失败测试点或放宽制作验收。

## 任务 8：清理残留并更新架构约定

**修改：** `src/domain/parameterMetrics.ts`、`src/library/PublishedTemplateLibrary.tsx`、`src/viewport/ComponentViewport.tsx`、`tsconfig.json`、`docs/CODE-ARCHITECTURE.md`。

- [x] 删除未调用的 densityInKgPerCubicMetre、alreadyAdded。
- [x] 检查 PreviewScene 未使用的 connectedInterfaces：若当前连通高亮应传给 MemberModel，则补齐传递并验证画布表现；确为废弃时才删除参数。不能把所有未使用声明视为可直接删除。
- [x] 删除或改名无实际用途的 handleTransformPreview 参数，保持上层回调类型兼容。
- [x] 启用 noUnusedLocals、noUnusedParameters；TypeScript 内建检查覆盖即可。
- [x] 文档写清楚：领域层允许依赖 Three.js 数学对象，浏览器存储和网络访问在适配边界；补充批量事务、统一附件来源与 CAD 加载路径。

**验收：** 默认 build 可阻止新增未使用声明；文档依赖方向与真实 imports 一致。

## 检查命令与完成条件

按任务先运行对应小范围测试；同一状态已有有效结果时复用。任务完成后运行：

```powershell
npm test
npm run build
.venv/Scripts/python.exe -m unittest backend.tests.test_components backend.tests.test_collaboration backend.tests.test_mcp_features backend.tests.test_mcp_assembly -v
.venv/Scripts/python.exe -m unittest backend.tests.test_geometry_resize backend.tests.test_geometry_resize_api backend.tests.test_resize_inspection_cache backend.tests.test_brace_policy -v
```

修改求解器、制作验收或来源加载后，额外运行真实 CAD 与交付回归：

```powershell
.venv/Scripts/python.exe -m unittest backend.tests.test_geometry_resize_cad backend.tests.test_builtin_braces backend.tests.test_qualification backend.tests.test_mcp_authoring_workflow -v
```

最后做一次后端完整回归：

```powershell
.venv/Scripts/python.exe -m unittest discover -s backend/tests -v
```

浏览器验收至少覆盖：上传附件并导出、连接成功/被拒绝、快速连续输入、多选复制粘贴/撤销、切换草稿、几何预览应用/撤销、验收后发布。真实 CAD 检查耗时较长；未完成时必须明确列为未验证，不以定向测试替代完整交付证据。


## 实施记录（2026-11-28）

八项任务已落地，改动保留在当前工作区，未提交或推送。实施时补充了三个必要约束：全量 model.restore 要求精确修订；撤销快照失去模型基线时失效，防止覆盖并发 Agent 模型编辑；上传结果被当前草稿拒绝时，只清理未被后端文档引用的文件并释放配额。

最终验证：前端 156 项测试通过，TypeScript 严格未使用检查与构建通过。后端完整发现测试 286 项通过（包含真实 CAD、撑杆与 MCP 制作交付）；收尾的上传、修订和撤销修正另跑 54 项定向测试通过。固定发布包字节哈希和两端动作逐步文档一致性均有回归断言。

独立浏览器/SQLite 验收确认上传后完整导出、断网恢复副本及重试、新建刷新、成员复制撤销和精确位姿保存；真实 CAD 窗口确认缩短、关闭补齐的普通高度调整、重新启用补齐，以及弹窗 Esc 关闭。测试资料和日志位于 .runtime/architecture-cleanup-*，不使用用户草稿库。
