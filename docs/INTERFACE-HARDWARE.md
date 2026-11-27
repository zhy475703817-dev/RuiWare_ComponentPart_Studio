# 接口连接类型与五金配置

更新日期：2026-11-26。

## 使用原则

五金尺寸是用户设置值。**不读取接口尺寸、不检查适用性、不按接口类型或尺寸过滤预设。**所有连接场景均可选；接口类型仅影响场景的排列顺序。预设之外的螺纹、长度、材质、型号均可手动填写，保存后保留用户输入。

平台检查已连接节点“有没有填写”：需要五金时，必须填写真实类别/名称、非占位规格、整数数量、选定场景的清单类别；无需五金时，必须明确场景及原因。同一共用节点的声明须一致。未完成不能发布或标记交付完成，未连接的预留接口不强制配置。平台不比较螺栓直径与孔径，不验证螺母螺纹配合，不根据接口尺寸推荐长度，也不判断承载或装配适用性。数字组合不在预设中不会因此阻止保存或发布。

## 平台可读取的数据

| 文件/入口 | 用途 |
| --- | --- |
| [hardwareSelectionRules.json](../src/domain/hardwareSelectionRules.json) | 前后端共用的 25 个连接场景、清单类别和完成状态规则 |
| [hardwareInputPresets.json](../src/domain/hardwareInputPresets.json) | 14 类市售类型/常用尺寸输入预设、格式模板与厂商资料链接 |
| [interface-hardware-catalog.v1.json](interface-hardware-catalog.v1.json) | 14 种接口、10 种连接形式（含自定义）、15 个脚本清单方案 |
| `component_get_hardware_selection_rules` | MCP：读取连接场景 |
| `component_get_hardware_input_presets` | MCP：读取尺寸/型号预设 |
| `component_set_interface_hardware` | MCP：保存接口五金配置，返回逐节点完成情况 |
| `GET /api/component-drafts/{draftId}` | 返回 `hardwareSelection` 逐节点结果与 `checks` |

JSON 参考目录不是组件草稿，不能整份提交到草稿创建接口。场景和预设已在界面原生使用；AI/脚本可通过 MCP 读取。目录方案 ID（如 `bolt-kit`）与连接场景 ID（如 `bolt-through`）不是同一字段。

## 网页操作

1. 在“接口”阶段选择成员接口，进入“接口五金”。
2. 选择实际连接场景；已有手动清单保留，并补入该场景缺少的类别行。无需额外五金的场景切换为空清单。
3. 每行展开“从常用规格填写”，选择五金类型，选择或键入螺纹、长度等。点击“填入此行（保留数量）”后才改动该行，可继续修改规格文字。
4. 填写数量及必要说明，点击“保存五金配置”。填写未完成时可保存草稿，界面保留待确认提示。名称为空或手动清单无行时，先补行再保存。
5. “五金配置”完成情况默认收起，仅显示待确认数量。点击摘要展开已指定、无需额外五金、待确认的节点数和清单；再次点击可收起。展开待确认节点查看原因并定位顶层接口。深层内部接口显示来源路径。

未明确尺寸的已有数据不会被静默套用默认规格。自动推荐只作历史参考，不能代表已经填写完成。配置清单不等同于采购订单。

## 全部接口类型

下表只给出场景线索，不限制可选项。未分类、未知 ID 和 `custom:*` 接口也能选择全部场景，或选择自定义五金。

| 接口 ID | 显示名称 | 常见场景 ID（全部场景仍可选） |
| --- | --- | --- |
| `bolt.male.v1` | 螺栓连接公口 | `bolt-through`、`bolt-into-thread`、`screw-into-thread` |
| `bolt.female.v1` | 螺栓连接母口 | `bolt-through`、`bolt-into-thread`、`screw-into-thread` |
| `threaded.stud.v1` | 螺纹公端 / 螺柱 | `bolt-into-thread`、`screw-into-thread`、`stud-separate`、`stud-integral`、`thread-direct` |
| `threaded.hole.v1` | 螺纹母端 / 螺纹孔 | `bolt-into-thread`、`screw-into-thread`、`stud-separate`、`stud-integral`、`thread-direct` |
| `pin.v1` | 定位销 | `bolt-through`、`pin-separate`、`pin-integral` |
| `hole.v1` | 定位孔 | `bolt-through`、`pin-separate`、`pin-integral` |
| `weld.surface.v1` | 焊接面 | `weld-only` |
| `snap.hook.v1` | 卡扣公口 / 卡钩 | `snap-integral`、`snap-separate` |
| `snap.slot.v1` | 卡扣母口 / 卡槽 | `snap-integral`、`snap-separate` |
| `rail.v1` | 导轨 | `rail-slide`、`rail-fastening`、`rail-stop` |
| `rail.slot.v1` | 导轨槽 | `rail-slide`、`rail-fastening`、`rail-stop` |
| `plane.contact.v1` | 平面贴合面 | `bolt-through`、`bolt-into-thread`、`screw-into-thread`、`plane-contact`、`plane-glued` |
| `shaft.v1` | 轴 / 轴颈 | `shaft-direct`、`shaft-bearing`、`shaft-bushing`、`shaft-key`、`shaft-ring` |
| `bore.v1` | 轴孔 / 轴承孔 | `shaft-direct`、`shaft-bearing`、`shaft-bushing`、`shaft-key`、`shaft-ring` |

## 定义阶段的连接形式

组件级连接形式只作描述，不会自动逐接口填写五金。支持：螺栓连接、焊接、卡扣连接、插接、铆接、胶接、型材槽连接、销钉连接、插接/卡扣、自定义。

焊接、胶接可说明无需额外五金；独立卡扣、销、铆钉、紧固件等按真实清单填写。五金数量单位是件，不以整数件数代替焊材/胶粘剂的质量或体积。

## 连接场景清单

类别用于填写完整性提示；尺寸由用户决定。可选附件不会自动加入。无需额外五金或自定义场景须说明原因，例如本体一体结构、已由某个装配成员提供或直接焊接。

| 场景 ID | 场景 | 必填类别 | 可选类别 | 说明必填 |
| --- | --- | --- | --- | --- |
| `bolt-through` | 穿透螺栓＋独立螺母 | 螺栓、螺母 | 垫圈、锁紧件 | 否 |
| `bolt-into-thread` | 螺栓拧入已有螺纹孔 | 螺栓 | 垫圈、锁紧件 | 否 |
| `screw-into-thread` | 螺钉拧入已有螺纹孔 | 螺钉 | 垫圈、锁紧件 | 否 |
| `stud-separate` | 独立螺柱＋螺母 | 螺柱、螺母 | 垫圈、锁紧件 | 否 |
| `stud-integral` | 本体已有螺柱，仅配螺母 | 螺母 | 垫圈、锁紧件 | 否 |
| `thread-direct` | 两本体螺纹直接旋合 | 无需额外五金 | — | 是 |
| `pin-separate` | 独立定位销 | 销 | 挡圈、垫圈 | 否 |
| `pin-integral` | 本体已有定位销 | 无需额外五金 | — | 是 |
| `weld-only` | 焊接且无需额外连接件 | 无需额外五金 | — | 是 |
| `snap-integral` | 本体卡钩与卡槽直接扣合 | 无需额外五金 | — | 是 |
| `snap-separate` | 独立卡扣件 | 卡扣件 | 螺钉、垫圈 | 否 |
| `rail-slide` | 导轨与槽直接滑动配合 | 无需额外五金 | — | 是 |
| `rail-fastening` | 槽内螺栓＋槽螺母紧固 | 螺栓、螺母 | 垫圈、连接件 | 否 |
| `rail-stop` | 独立导轨限位件 | 导轨附件 | 螺栓、螺钉、螺母、垫圈 | 否 |
| `plane-contact` | 单纯面贴合/支承 | 无需额外五金 | — | 是 |
| `plane-glued` | 胶接且无需额外机械连接件 | 无需额外五金 | — | 是 |
| `shaft-direct` | 轴与孔直接配合 | 无需额外五金 | — | 是 |
| `shaft-bearing` | 轴承支承 | 轴承 | 挡圈、轴套、垫圈、密封件 | 否 |
| `shaft-bushing` | 轴套支承 | 轴套 | 挡圈、垫圈 | 否 |
| `shaft-key` | 键连接 | 键 | 挡圈、轴承、轴套 | 否 |
| `shaft-ring` | 独立挡圈定位 | 挡圈 | 垫圈、轴承、轴套 | 否 |
| `rivet` | 铆接 | 铆钉 | 垫圈 | 是 |
| `included-in-member` | 所需五金已包含在本体或装配成员 | 无需额外五金 | — | 是 |
| `custom-required` | 经核对的自定义五金清单 | 自定义类别 | — | 是 |
| `custom-none` | 经核对确实无需额外五金 | 无需额外五金 | — | 是 |

## 尺寸与型号输入预设

所有字段支持自由输入。尺寸列表是常用输入选项；不是厂家库存的笛卡尔积，不承诺所有螺纹×长度×材质组合都存在。标准系列和厂家型号均有来源链接。用户填写的自定义值不会被接口数据改写。

| 类型 | 系列/参考 | 可填写字段 |
| --- | --- | --- |
| 六角螺栓 | ISO 4017 / DIN 933 | 螺纹、长度（mm）、等级/材质 |
| 内六角圆柱头螺钉 | ISO 4762 | 螺纹、长度（mm）、等级/材质 |
| 螺柱 | DIN 6379 | 螺纹、长度（mm） |
| 六角螺母 | DIN 934 | 螺纹、等级/材质 |
| 平垫圈 | ISO 7089 | 螺纹、材质 |
| 定位销 | DIN 6325 | 直径（mm）、长度（mm）、公差标记 |
| T型槽螺母 | 型材槽螺母 | 槽系列、螺纹、材质/表面 |
| 深沟球轴承 | 轴承型号 | 型号 |
| 滑动轴套 | igus GSM | 型号（内径×外径×长） |
| 轴用弹性挡圈 | DIN 471 | 公称尺寸（mm）、材质 |
| 平键 | DIN 6885 A | 宽×高（mm）、长度（mm） |
| 抽芯铆钉 | 抽芯铆钉型号 | 型号 |
| 搭扣锁扣 | GN 8330 | 型号 |
| 导轨限位件 | item Limit Stop 8 | 型号 |

常用螺栓/螺钉输入包括 M3、M4、M5、M6、M8、M10、M12、M14、M16、M20；常用长度 10、12、16、20、25、30、35、40、45、50、60、70、80、90、100、120 mm。可直接输入如 `M7×0.75` 或其他自定义螺纹，以及 `37.5` 等自定义长度；这些示例用于证明自由输入，不表示工程选型建议。销的预设使用 Ø 标记，不将销直径写成螺纹 M。

## 原生保存格式

内部草稿路径：`assembly.members[].interfaces[].hardware`。导出路径：`stages.assembly.members[].interfaces[].hardware`。以下只是格式示例，不会自动写入任何草稿：

```json
{
  "mode": "manual",
  "scenarioId": "bolt-into-thread",
  "note": "用户选择的连接配置",
  "items": [
    {"category": "螺栓", "name": "六角螺栓", "specification": "M8 × 30 mm", "quantity": 1}
  ]
}
```

`mode` 支持 `auto/manual/none`；`scenarioId` 最长 80 字符，`note` 最长 500 字符。每接口最多 50 行五金；类别/名称最长 80 字符，规格最长 120 字符，数量为 1–10000 的整数。无需五金用 `mode: "none"`、对应场景、非空说明及空 `items`。

后端拒绝未知字段。类型预设的 `standard`、`source`、`fields` 是输入辅助元数据，不直接写入 `HardwareItem`，生成的文字规格写入 `specification`。

## REST、MCP 和离线转换

REST 保存：`POST /api/component-drafts/{draftId}/actions`，请求体：

```json
{
  "expectedRevision": 7,
  "action": {
    "type": "interface.hardware.set",
    "memberId": "真实成员ID",
    "interfaceId": "真实接口ID",
    "hardware": {
      "mode": "none",
      "scenarioId": "weld-only",
      "note": "该处直接焊接，无额外五金",
      "items": []
    }
  }
}
```

MCP setter 使用相同 `draftId`、`expectedRevision`、`memberId`、`interfaceId` 和 `hardware`。先读取真实草稿与修订号；每次操作后采用服务器返回的新修订。动作替换整个接口五金配置，并同步该接口对应的对外端口快照；不会自动追加第二个方案。

离线脚本接受内部草稿、GET 响应或导出 JSON，只生成一个请求，不联网、不写数据库、不发布：

```powershell
node scripts/prepare-interface-hardware.mjs --list
node scripts/prepare-interface-hardware.mjs --draft draft.json --member MEMBER_ID --interface INTERFACE_ID --profile dowel-pin --scenario pin-separate --set "pinSpec=Ø8 × 30 mm" --output hardware-action.json
node scripts/prepare-interface-hardware.mjs --draft draft.json --member MEMBER_ID --interface INTERFACE_ID --profile none --scenario weld-only --note "该处直接焊接，无额外五金" --output hardware-none-action.json
```

上述 ID 与规格仅作语法示例。`--scenario` 与 `--note` 为新增选项；不提供场景的旧请求仍可读取/保存，但完成检查保持待确认。脚本不根据接口尺寸选择、过滤或校验规格。输出已有文件时拒绝覆盖。

## 完成状态、统计与发布

- 仅对已连接节点强制检查，包含普通连接、几何连接及子组件内部连接。`required` 或对外开放但尚未连接的预留接口不强制填写五金；对外端口映射仍用于识别同一物理节点及继承覆盖。
- 共用节点按同一物理端点合并。外层明确配置覆盖同源继承配置；同节点任一端明确配置可覆盖另一端自动参考。
- 同节点同时明确“需要五金”和“无需五金”，或两端清单规格/数量不同，显示声明冲突；不以取最大值的统计结果替代确认。这里检查的是同一节点重复声明是否一致，不是尺寸是否适用。
- 失效端点会报待确认，不把失效连接的零件数误认为无需五金。
- BOM 统计基于实际连接：重复行相加，同一节点同类别/名称/规格取最大数量，不同独立节点相加；未连接接口的预配置不形成实际用量，也不阻止五金配置完成。
- 自动推荐、旧配置缺场景、`待确认/长度待定` 等占位规格均为待确认；没有自动推荐结果不等于无需五金。
- 草稿允许逐步连接和保存待确认状态；新建连接、几何联动新增连接或删除所需五金后，立即重新检查。发布及 `deliveryReady` 要求所有已连接节点的五金配置完整，同时仍需原有几何制作验收。后端独立拒绝缺五金的发布请求，不能绕过网页按钮限制。自定义尺寸本身不影响完整性状态。
- 旧数据保持可读，不迁移真实规格、不修改已有发布包。历史自动推荐函数保留兼容行为（包括依赖旧尺寸元数据的显示），新规格预设与完成检查均不调用该函数来设置尺寸。

## 验证

`npm test` 包含目录一致性、尺寸自由输入、保存/导出、共享节点、子组件映射及前后端规则一致性测试。`npm run build` 检查 TypeScript 与打包。后端 `backend.tests.test_hardware_selection` 验证模型、保存回读、端口快照、发布完成状态和 MCP 工具。

本次修改实现配置流程和输入预设，不会替用户猜定已有草稿中的实际规格。
