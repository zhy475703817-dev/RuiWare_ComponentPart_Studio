# 发布模型预览实施计划

**Goal:** 组件平台读取已发布 rwpart 中的 STL 并在画布显示真实模型。
**Architecture:** library 选择 → preview API → 安全下载和 ZIP 解析 → 校验模型缓存 → STLLoader / viewport。
**Tech Stack:** 现有 FastAPI、httpx、标准库 zipfile/hashlib，React、Three.js STLLoader。
**Spec:** docs/superpowers/specs/2026-10-17-part-preview-design.md

## 约束

在当前工作区保留已有工作。只读访问零部件平台。不修改源平台，不发布/覆盖真实模板，不新增依赖。不实现 CAD 求解或加入装配。

## Task 1 — 包读取与后端预览

- [x] 写 backend/tests/test_preview.py，覆盖包内模型、元数据/模型校验、ZIP限制、旧模型受控回退、缓存、下载错误与重试。
- [x] 确认新增测试在未实现时失败。
- [x] 复用 library 的固定上游配置/请求错误映射，新增 backend/app/preview.py，生命周期初始化缓存并挂载路由。
- [x] 运行后端全部测试。

## Task 2 — 前端预览与验证

- [x] 写 tests/template-preview.test.mjs 验证预览描述、同源模型 URL、STL解析、网格边界和错误响应。
- [x] 实现 src/library/templatePreview.ts、useTemplatePreview.ts 和视图中的相机适配、状态/重试，保留草稿及详情交互。
- [x] 更新 README、架构说明与两平台包契约。
- [x] 运行前后端测试与构建，用隔离上游 fixture 经实际 FastAPI 验证新包显示、切换竞态、重试、重置，检查截图；核对真实发布库表现。
- [x] 只读代码复核并修复重要问题，保留服务正常运行。

