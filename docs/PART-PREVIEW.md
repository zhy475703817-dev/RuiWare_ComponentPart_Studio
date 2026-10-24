# 发布包模型预览契约

组件平台只读消费已发布 `.rwpart`（ZIP），不修改源模板或发布记录。点击模板后，组件平台在本机从包内参数、规则、材料和几何定义生成预览模型；用户只看到最终模型和模板允许调整的参数。该预览不会加入装配或改变草稿修订。

## 本地模型重建

左侧通过 `GET /api/template-library` 读取本地快照，后台同步在接纳新零件版本前使用包内默认参数及标称材料实际执行重建，并要求 CAD 成功且几何有效。缺少定义、参数无法求值或几何生成失败的版本不出现在左侧。`GET /api/published-templates/{publicationId}/availability?sha256={整包哈希}` 保留，返回同一检查逻辑的 `available` 结果。包内是否带 STL 不作为准入条件，`previewModel/reconstructionGuide` 发布附加字段不进入 CAD 草稿。

首次成功项按原发布时间顺序逐步显示，后续启动先显示 SQLite 中的现有列表；后台约五分钟检查一次，新列表等待用户应用。检查和预览共用单个 CAD 并发槽。SQLite 按连接来源、发布 ID、包哈希和引擎版本保存默认模型或确定失败结果，后端重启保留；原有 128 MiB / 1000 条内存缓存继续作为运行期缓存。包哈希、引擎代码或相关依赖变化会重新检查。连接失败、超时和服务故障不缓存为模板失败；刷新可重试。已检查的默认模型可直接用于画布，修改参数后仍单独校验重建。准入只保证默认参数可生成模型，不承诺任意参数组合都可重建。

发布包无需携带 STL。包内需提供 `manifest.json`、`parameters.json`、`geometry-recipe.json`、`feature-rules.json`、`constraints.json`、`sweep-path.json`、`interfaces.json`、`variants.json`、`material-requirements.json`、`material-validation.json`、`evidence.json` 和 `admission.json`。组件平台校验整包 SHA-256 后，把这些定义映射到随项目部署的 CAD 引擎，在独立子进程中生成 STL；生成结果只用于当前预览，不会回写发布库。

`GET /api/published-templates/{publicationId}/reconstruction?sha256={整包哈希}` 返回固定版本和可调整参数。只有 `exposed=true`、用户输入来源且作用域为模板或零件实例的参数可调整；常量、公式和材料绑定参数不向用户开放。`POST /api/published-templates/{publicationId}/reconstruction/model?sha256={整包哈希}` 使用 `{ "parameters": { "length": 120 } }` 生成 STL。后端限制请求体 64 KiB、单个重建并发，CAD 子进程 90 秒超时，返回 `X-RuiWare-SHA256` 校验模型字节。每次生成在 `.runtime` 下使用隔离临时目录，完成后清理。

组件平台运行环境需安装 `backend/requirements.txt` 中的 NumPy 与 `cadquery-ocp`。Windows 一键启动会根据依赖文件变化自动安装或更新依赖。重建失败时不会用包内或旧地址的 STL 掩盖失败；页面只显示简明的用户提示。

## 发布包要求

```text
manifest.json
parameters.json
interfaces.json
outputs.json
geometry/preview.stl
```

`manifest.json` 使用 `schemaVersion: "3.0"`、`unitSystem: "mm-kg-s"`、`coordinateSystem: "right-handed-z-up"`，`id/code` 与发布记录的 `templateId/code` 一致。接口文件需声明相同坐标系。参数读取 `parameters.json.definitions`，接口读取 `interfaces.json.interfaces`，原始声明保留，不推测物理装配坐标。

`outputs.json.success` 必须为 true，`artifacts` 恰有一条 STL 输出：

```json
{
  "kind": "stl",
  "url": "/artifacts/0123456789abcdef/preview.stl",
  "packagePath": "geometry/preview.stl",
  "size": 123456,
  "sha256": "STL 文件实际的 64 位十六进制 SHA-256"
}
```

新格式读取 `packagePath`，不依赖 `url`。包内路径目前只支持 `geometry/preview.stl`。整包实际字节数、SHA-256 必须与发布元数据一致，STL 字节数、SHA-256 必须与 artifact 一致。新格式损坏时直接报错，不转用旧地址。

旧包无 `packagePath` 时，仅允许固定配置上游的 `/artifacts/<16至64位十六进制>/preview.stl`，不接受任意 URL、不跟随重定向，同样校验 STL 哈希。不存在则返回 `MODEL_MISSING`，需要在零部件平台发布包含 STL 的新版本；组件平台不修补或覆盖历史发布对象。

## API

保留的兼容预览接口 `GET /api/published-templates/{publicationId}/preview?sha256={整包哈希}` 返回：

- `publication`：固定发布记录。
- `unitSystem/coordinateSystem`：单位与工程坐标。
- `parameters/interfaces`：原始定义数组。
- `model`：`format: stl`、`source: package|legacy`、同源 `url`、模型 `size/sha256`。

`GET /api/published-templates/{publicationId}/preview/model?sha256={整包哈希}` 返回 `model/stl`，附加 `X-RuiWare-SHA256`、`X-Content-Type-Options: nosniff`。模型 URL 的哈希代表包版本，响应头哈希代表 STL 文件。前端对描述中的发布版本及模型下载再次校验。

上游元数据始终重新核对。若发布哈希变化返回 409 `PUBLICATION_CHANGED`。包和模型损坏返回 502 `PACKAGE_INTEGRITY_ERROR/MODEL_INTEGRITY_ERROR/PACKAGE_INVALID`；无模型或不支持格式返回 422 `MODEL_MISSING/PACKAGE_UNSUPPORTED`。错误不返回上游调试信息。

## 资源限制与生命周期

单包/模型最多 64 MiB，ZIP 总声明展开最多 128 MiB、256 条目、单 JSON 2 MiB，仅接受 STORE/DEFLATE，不写入磁盘。不接受重复路径、穿越、反斜杠、符号链接和加密条目。包读取链路总超时 30 秒（含等待），每次 HTTP 读取空闲超时 10 秒；最多两个并行加载。成功结果缓存按发布 ID/包哈希索引，模型及序列化描述计入 128 MiB 上限，后端重启清空。失败不缓存。

前端保留原点、毫米、右手 Z 向上，加载后根据网格边界适配相机和网格尺寸。STL 解析拒绝空网格、非有限坐标和全退化三角形。切换取消旧请求，迟到结果不覆盖新选择；新建/切换/卸载释放几何，材质随场景卸载释放。预览结果不进入组件导出。

本轮验证：后端契约测试、前端解析测试，以及独立测试服务中的真实 CAD STL 经 ZIP → FastAPI → 浏览器显示。独立服务不向真实共享库写入测试发布。真实旧包若模型不存在，页面准确显示需发布新版本。
