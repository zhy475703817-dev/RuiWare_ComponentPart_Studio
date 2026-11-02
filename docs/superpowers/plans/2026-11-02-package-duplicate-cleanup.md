# 组件平台发布库重复与旧版本清理

本轮范围仅限 `D:\postgraduate_project\component`。零部件模板平台不作任何修改。相似度规则沿用 [清理设计](../specs/2026-11-02-package-duplicate-cleanup-design.md)，但不实施其中零部件平台的发布前检查和发布后清理。

## 目标与规则

- 组件平台直接扫描共享 MinIO；只允许清理 `templateKind=part`，缺少类型的历史记录按零件处理。
- 同一 `templateId` 保留 `(version, createdAt, publicationId)` 排序最高的发布；同包 SHA-256 的组只留最新发布。
- 高相似分数满足 `S >= 90, G >= 95, P >= 90`，必须用户逐项确认；疑似相似 (`S >= 80` 或 `G >= 95, P >= 85`) 只提示。
- 固定引用仅展示影响，不保护包；用户已接受被删版本引用的组件可能无法重新加载。
- 本机备份校验成功之后才能删除。备份保存于组件平台数据目录 30 天，到期由用户手工清理。
- 扫描不完整、对象身份或路径变化、备份失败、权限失败都不能报告为删除成功。

## 实现

- `backend/app/package_similarity.py`：受限 ZIP 安全读取、支持的 manifest/单位/坐标校验、稳定字段规范化、几何与参数相似度和固定阈值。
- `backend/app/published_storage.py`：发布身份与 key 复核、读取并校验包、版本/删除标记枚举与删除验证、失败后的备份补偿；恢复使用 S3 条件创建，不覆盖并发写入。
- `backend/app/cleanup_store.py`：24 小时扫描单、本机备份原子落盘及哈希校验、30 天到期信息、JSONL 审计。
- `backend/app/library_cleanup.py`：完整候选分类、引用影响、重新扫描及身份比较、串行清理、恢复/审计接口。
- `backend/app/library_sync.py`、`backend/app/library_cache.py`：MinIO 同步时自动处理严格候选，删除成功后立即从活动/待应用目录移除；不删除本机原始包与模型缓存。
- `src/library/StorageReview.tsx`：评分与差异展示、逐项确认、清理结果、备份下载/恢复/过期清理、审计导出。

## 验证

- 后端：`python -m unittest discover -s backend/tests`
- 前端：`npm test`、`npm run build`
- 回归覆盖引用明确仍允许清理、旧版优先于最新重复包、部分删除失败补偿后重试、扫描后 key 变化拒绝、并发占用时条件恢复拒绝覆盖、版本化删除和备份恢复。
- 不在生产 MinIO 上执行破坏性测试或清理；本地自动化测试只使用内存 MinIO 替身。

## 尚需部署验证

MinIO 的版本化删除权限及 `If-None-Match: *` 条件写入兼容性尚未对真实 MinIO 进行集成验证。组件平台使用账户需要能枚举发布对象版本、删除相应对象版本，以及在原 key 不存在时执行条件创建。上线前应在隔离测试前缀验证权限和客户端版本行为。
