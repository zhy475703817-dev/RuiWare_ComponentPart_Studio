# Component Architecture Implementation Plan

**Goal:** 将现有工作台整理为七阶段、共享领域数据及独立视图，并保留发布库交互。

**Architecture:** app 统一文档与页面状态，domain 提供纯数据操作，stages 使用明确的 props 编辑同一文档。library 与 viewport 各自管理读取和渲染职责。

**Tech Stack:** React 19、TypeScript、Vite、现有 Three.js 依赖、Node 24 原生测试；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-17-component-architecture-design.md`

## 全局约束

- 在当前工作区保留前面未提交的发布库实现及原有缓存变动。
- 不修改零部件平台，不实现包解析、模型预览、CAD、求解器或正式发布。
- 不把浏览器状态、运行时缓存或密钥写入组件导出。

## Task 1 — 领域数据与操作

- [x] 新增 `tests/component-domain.test.mjs`，运行 `node --test tests/component-domain.test.mjs` 观察缺失功能失败。
- [x] 实现 `src/domain/component.ts` 的文档、成员、固定模板引用、结构化连接、参数、绑定、规则和槽位类型。
- [x] 实现 `componentReducer.ts` 的编辑与级联清理；`validation.ts` 返回 pass/fail/pending 检查；`serialization.ts` 生成兼容主体结构的草稿 JSON。
- [x] 验证字段编辑不丢其他数据、不同实例共享同一模板版本、删除成员清理引用、失效引用被验证阻止、导出不携带 UI 数据。

## Task 2 — 七阶段与工作台

- [x] `src/app/useComponentWorkspace.ts` 持有文档 reducer 和阶段、成员选择、预览模板、状态消息；导出副作用留在 hook。
- [x] `src/app/stages.ts` 统一阶段标识和名称，七个 `src/stages/*Stage.tsx` 只接收所需数据与编辑回调。
- [x] 将列表移入 `src/library`，保留展开状态本地管理，通过 `onPreview` 回调通知工作台所选发布记录；更新现有测试导入。
- [x] 将画布移入 `src/viewport/ComponentViewport.tsx`，布局移入 `WorkspaceLayout.tsx`，样式移入 `src/styles/workspace.css`。
- [x] `App.tsx` 负责面板组合，`main.tsx` 仅启动；面板明确指出装配求解、规则执行及正式发布未接入。

## Task 3 — 验证和文档

- [x] 执行 `npm test`、`npm run build` 和差异空白检查。
- [x] 浏览器验证真实库、搜索、详情折叠、七阶段、定义字段和参数跨阶段保留、新建重置、导出字段与未实现能力提示。
- [x] 更新 README 和代码架构说明，标明阶段责任、依赖方向、扩展模型预览的位置及现有实现边界。
- [x] 进行只读代码复核，修复重要问题并报告最终结果。

