import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { PRESET_INTERFACE_TYPES } from '../src/domain/interfaceTypes.ts';
import { auditHardwareSelection, assessInterfaceHardware, hardwareScenarios, HARDWARE_SCENARIOS } from '../src/domain/hardwareSelection.ts';
import { HARDWARE_INPUT_PRESETS, formatHardwarePreset } from '../src/domain/hardwareInputPresets.ts';
import { summarizeHardwareUsage } from '../src/domain/hardware.ts';

const bolt = { mode: 'manual', scenarioId: 'bolt-into-thread', items: [{ category: '螺栓', name: '六角螺栓', specification: 'M8 × 30 mm', quantity: 2 }] };
const none = { mode: 'none', scenarioId: 'plane-contact', items: [], note: '仅贴合，由外围已配置的支架固定' };
const member = (id, hardware) => ({ id, name: id, code: 'PART', role: '',
  template: { publicationId: 'p', templateId: 't', version: 1, sha256: 'a'.repeat(64) },
  parameterValues: {}, position: [0, 0, 0], rotation: [0, 0, 0],
  interfaces: [{ id: 'i', name: '连接端', type: 'plane', tags: [], connectionTypeId: 'bolt.female.v1', ...(hardware ? { hardware: structuredClone(hardware) } : {}) }] });
const mate = (a, b) => ({ id: `${a}-${b}`, from: { memberId: a, interfaceId: 'i' }, to: { memberId: b, interfaceId: 'i' }, relation: '贴合', offset: 0 });
function doc(a, b) { const d = createComponentDocument(); d.assembly = { members: [member('a', a), member('b', b)], mates: [mate('a', 'b')] }; return d; }
const audit = d => auditHardwareSelection(d.assembly.members, d.assembly.mates, d.geometryDesign, d.ports);

test('全部接口类型都有场景提示且全部场景可选，不进行类型适用检查', () => {
  for (const type of PRESET_INTERFACE_TYPES) {
    assert.ok(HARDWARE_SCENARIOS.some(s => s.connectionTypeIds.includes(type.id)), type.id);
    const item = { ...member('a').interfaces[0], connectionTypeId: type.id };
    assert.equal(hardwareScenarios(item).length, HARDWARE_SCENARIOS.length);
    assert.equal(assessInterfaceHardware(item, bolt).status, 'configured');
  }
  assert.equal(hardwareScenarios({ ...member('a').interfaces[0], connectionTypeId: 'custom:未分类' }).length, HARDWARE_SCENARIOS.length);
});

test('预设各类型可格式化；自定义螺纹和非库存组合不受筛选', () => {
  assert.equal(HARDWARE_INPUT_PRESETS.interfaceDimensionChecks, false);
  assert.equal(new Set(HARDWARE_INPUT_PRESETS.families.map(f => f.id)).size, HARDWARE_INPUT_PRESETS.families.length);
  for (const family of HARDWARE_INPUT_PRESETS.families) {
    const values = Object.fromEntries(family.fields.map(field => [field.id, field.values[0]]));
    const preset = formatHardwarePreset(family.id, values);
    assert.ok(preset.specification && !/[{}]/.test(preset.specification), family.id);
  }
  const custom = formatHardwarePreset('hex-bolt', { thread: 'M7×0.75', length: '37.5', grade: '自定材料' });
  assert.equal(custom.specification, 'ISO 4017 / M7×0.75 × 37.5 mm / 自定材料');
  assert.throws(() => formatHardwarePreset('hex-bolt', { thread: 'M8' }), /请填写/);
});

test('无尺寸、不同尺寸均接受同一自定义五金配置，保存及导出不改写规格', () => {
  for (const size of [undefined, { diameter: 1, unit: 'mm' }, { diameter: 900, unit: 'in' }]) {
    let d = doc(); d.assembly.members[0].interfaces[0].interfaceSize = size;
    const hardware = { ...bolt, note: '用户设置', items: [{ ...bolt.items[0], ...formatHardwarePreset('hex-bolt', { thread: 'M7×0.75', length: '37.5', grade: '自定材料' }) }] };
    d = componentReducer(d, { type: 'interface.hardware.set', memberId: 'a', interfaceId: 'i', hardware });
    assert.deepEqual(d.assembly.members[0].interfaces[0].hardware, hardware);
    assert.equal(audit(d).complete, true);
    assert.deepEqual(JSON.parse(serializeComponent(d)).stages.assembly.members[0].interfaces[0].hardware, hardware);
  }
});

test('自动、旧数据和占位规格保持待确认；无需五金需要说明', () => {
  for (const config of [undefined, { mode: 'auto' }, { mode: 'manual', items: bolt.items }, { ...bolt, items: [{ ...bolt.items[0], specification: 'M8 × 长度待确认' }] }, { ...none, note: '' }]) {
    assert.equal(assessInterfaceHardware(member('a', config).interfaces[0]).status, 'pending');
  }
  assert.equal(assessInterfaceHardware(member('a', none).interfaces[0]).status, 'not-required');
  assert.equal(assessInterfaceHardware(member('a', { ...bolt, scenarioId: 'bolt-through' }).interfaces[0]).issues[0].code, 'REQUIRED_HARDWARE_MISSING');
});

test('同一节点显式配置覆盖自动参考；重复计数及冲突可追溯', () => {
  let d = doc(bolt); assert.equal(audit(d).configured, 1);
  d = doc(bolt, bolt); assert.equal(audit(d).total, 1); assert.equal(audit(d).complete, true);
  assert.equal(summarizeHardwareUsage(d.assembly.members, d.assembly.mates)[0].quantity, 2);
  d = doc(bolt, none); assert.ok(audit(d).entries[0].issues.some(i => i.code === 'SHARED_SELECTION_CONFLICT'));
  d = doc(bolt, { ...bolt, items: [{ ...bolt.items[0], quantity: 3 }] });
  assert.ok(audit(d).entries[0].issues.some(i => i.code === 'SHARED_KIT_CONFLICT'));
});

test('嵌套端口与源接口使用同一节点，外层明确配置覆盖继承值', () => {
  const child = doc(bolt);
  child.ports = [{ id: 'i', name: '端口', source: { memberId: 'a', interfaceId: 'i' }, tags: [] }];
  const d = doc({ ...bolt, items: [{ ...bolt.items[0], specification: 'M10 × 45 mm' }] });
  d.assembly.members[0].instanceDocument = child;
  assert.equal(audit(d).total, 1); assert.equal(audit(d).complete, true);
  assert.equal(summarizeHardwareUsage(d.assembly.members, d.assembly.mates)[0].specification, 'M10 × 45 mm');
  child.ports[0].source.interfaceId = 'missing';
  assert.ok(audit(d).entries.some(entry => entry.issues.some(issue => issue.code === 'INVALID_ENDPOINT')));
});

test('只强制检查已连接节点，未连接的必需或对外预留口不误报', () => {
  const d = doc(); d.assembly.mates = [];
  assert.equal(audit(d).total, 0);
  d.assembly.members[0].interfaces[0].required = true; assert.equal(audit(d).pending, 0);
  d.ports = [{ id: 'p', name: '对外', source: { memberId: 'b', interfaceId: 'i' }, tags: [] }];
  assert.equal(audit(d).pending, 0);
  d.ports[0].source.memberId = 'missing'; assert.equal(audit(d).pending, 0);
  d.assembly.mates = [mate('a', 'missing')];
  assert.equal(audit(d).pending, 1);
  assert.ok(audit(d).entries.some(entry => entry.issues.some(issue => issue.code === 'INVALID_ENDPOINT')));
});

test('新增连接立即成为必填五金节点；几何连接同样检查，断开后解除该节点要求', () => {
  const d = doc(); d.assembly.mates = [];
  assert.equal(audit(d).complete, true);
  d.geometryDesign = { relations: [{ id: 'ab', a: { path: ['a'], interfaceId: 'i' }, b: { path: ['b'], interfaceId: 'i' }, mode: 'preserve-offset' }] };
  assert.equal(audit(d).scope, 'connected');
  assert.equal(audit(d).pending, 1);
  d.assembly.members[0].interfaces[0].hardware = structuredClone(bolt);
  assert.equal(audit(d).complete, true);
  delete d.assembly.members[0].interfaces[0].hardware;
  assert.equal(audit(d).pending, 1);
  d.geometryDesign.relations = [];
  assert.equal(audit(d).complete, true);
});

test('只有子组件内部已连接时也必须配置，外层端口覆盖仍然生效', () => {
  const nested = doc();
  nested.ports = [{ id: 'i', name: '端口', source: { memberId: 'a', interfaceId: 'i' }, tags: [] }];
  const d = doc(); d.assembly.mates = []; d.assembly.members = [member('outer')];
  d.assembly.members[0].interfaces[0].required = true;
  d.assembly.members[0].instanceDocument = nested;
  assert.equal(audit(d).pending, 1);
  d.assembly.members[0].interfaces[0].hardware = structuredClone(none);
  assert.equal(audit(d).notRequired, 1);
  assert.equal(audit(d).complete, true);
});

test('所有场景和嵌套/冲突/缺失数据的前后端完成情况一致', () => {
  const documents = [doc(), doc(bolt), doc(none), doc(bolt, none), doc(bolt, { ...bolt, items: [{ ...bolt.items[0], quantity: 3 }] })];
  for (const scenario of HARDWARE_SCENARIOS) documents.push(doc({ mode: scenario.mode, scenarioId: scenario.id, note: '人工确定的连接说明', items: scenario.items.map(i => ({ ...i, specification: '用户自定 37.5 mm' })) }));
  const child = doc(bolt); child.ports = [{ id: 'i', name: '端口', source: { memberId: 'a', interfaceId: 'i' }, tags: [] }];
  const nested = doc(none); nested.assembly.members[0].instanceDocument = child; documents.push(nested);
  const broken = structuredClone(nested); broken.assembly.members[0].instanceDocument.ports[0].source.interfaceId = 'missing'; documents.push(broken);
  const invalid = doc(); invalid.assembly.mates[0].to.memberId = 'missing'; documents.push(invalid);
  const reserved = doc(); reserved.assembly.mates = []; reserved.assembly.members[0].interfaces[0].required = true;
  reserved.ports = [{ id: 'p', name: '预留', source: { memberId: 'a', interfaceId: 'i' }, tags: [] }]; documents.push(reserved);
  const result = spawnSync(process.env.HARDWARE_TEST_PYTHON || 'python', ['-X', 'utf8', '-c', 'import json,sys; from backend.app.components.hardware_selection import audit_hardware_selection; print(json.dumps([audit_hardware_selection(d) for d in json.load(sys.stdin)],ensure_ascii=False))'], { cwd: new URL('..', import.meta.url), input: JSON.stringify(documents), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  assert.deepEqual(JSON.parse(result.stdout), documents.map(audit));
});
