# 组件对外接口

第三步管理整个组件向上层暴露的连接契约。第二步仍负责内部装配；阶段顺序、来源接口资料、槽位、共享库和发布流程均保留。

## 人工操作

1. 在第三步点击“暴露接口”，选择已有成员接口，填写名称、标签、允许的连接方式和结构化尺寸规格。未知尺寸或公差留空，系统不会当作已匹配。
2. 在“基准确认”中核对成员局部连接点、XYZ 欧拉角和轴向。校准仅保存当前草稿实例的接口坐标，不改来源发布包。位置单位为 mm，页面角度为度，API 角度为弧度。
3. 选择另一个组件草稿及其对外接口，以双方修订号只读试配。当前支持固定连接的整体刚体对齐；正式装配位置不变。预览复用现有模型视图，名称以“本侧 / 目标”区分。
4. 检查双方关键尺寸、公差极值、带符号间隙、规则问题码和基准残差，保存服务端重算的报告，再记录人工复核依据。
5. “操作与诊断”提供命令、修订号、操作者、原因、前后差异和错误信息，可复制 JSON。来源参数、位姿、版本或基准变化会使确认过期；目标草稿更新会使旧试配报告过期。

“基准对齐通过”和“人工复核”都不表示实体无干涉。当前精确碰撞检查始终为 pending，并在页面与报告中明确显示。

## AI 操作

- `component_get_draft` 读取文档、`portDiagnostics`、`operations` 和当前 revision。
- `component_execute_action(draftId, expectedRevision, action, reason)` 执行 `port.add`、`port.patch`、`port.remove`、`interface.set` 或 `port.trial.record`。仅修改提供的字段；必须提交当前修订与依据。
- `component_trial_ports(draftId, expectedRevision, portId, options)` 只读试配。options 需要 targetDraftId、targetPortId、expectedTargetRevision；offset 为 mm，angle 为 rad，relation 当前为“固定”。
- REST 对应 `POST /api/component-drafts/{draftId}/actions` 与 `POST /api/component-drafts/{draftId}/ports/trial`，与 MCP 共用实现。
- AI 不能生成或导入人工基准确认、人工复核，也不能在新增或编辑契约时伪造服务端试配证据。修订冲突后重读并重新判断，不能强行覆盖。

诊断层分别表示定义、基准、双边规则、几何对齐/待碰撞、人工复核。缺失数据保持 pending，历史数据明确标记 stale。操作日志每草稿保留最近 100 条，响应提供最近 50 条；它不是永久审计归档。

## 验证

前端：`node --experimental-strip-types --test tests/*.test.mjs` 与 `npm run build`。

后端：安装现有 backend/requirements.txt，设置 `NODE_OPTIONS=--experimental-strip-types`（需要该标志的 Node 版本），运行 `python -m unittest discover -s backend/tests`。测试使用临时数据库与模拟共享存储，不需要真实发布密钥。
