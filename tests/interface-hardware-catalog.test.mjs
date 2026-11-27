import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { PRESET_INTERFACE_TYPES } from '../src/domain/interfaceTypes.ts';
import { recommendInterfaceHardware, summarizeHardwareUsage } from '../src/domain/hardware.ts';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { buildHardwareRequest, compileHardwareProfile } from '../scripts/prepare-interface-hardware.mjs';

const catalog = JSON.parse(readFileSync(new URL('../docs/interface-hardware-catalog.v1.json', import.meta.url), 'utf8'));
const options = { memberId: 'a', interfaceId: 'endpoint', profileId: 'auto' };
function draft() {
  const document = createComponentDocument();
  document.revision = 7;
  document.assembly.members = ['a', 'b'].map(id => ({
    id, name: id, code: id.toUpperCase(), role: '',
    template: { publicationId: 'test-only', templateId: 'test-only', version: 1, sha256: 'a'.repeat(64) },
    position: [0, 0, 0], rotation: [0, 0, 0], parameterValues: {},
    interfaces: [{
      id: 'endpoint', name: '连接端', type: 'plane', tags: [],
      connectionTypeId: 'bolt.female.v1', interfaceSize: { diameter: 8, unit: 'mm' },
      referenceFrame: { axis: 'z' },
      mating: { version: 1, family: 'plane-contact', role: 'contact-face',
        dimensions: {}, allowedRelations: ['贴合'], capacity: 1 },
    }],
  }));
  document.assembly.mates = [{ id: 'ab', from: { memberId: 'a', interfaceId: 'endpoint' },
    to: { memberId: 'b', interfaceId: 'endpoint' }, relation: '贴合', offset: 0 }];
  document.ports = [{ id: 'port', name: '对外端', source: { memberId: 'a', interfaceId: 'endpoint' },
    tags: [], interface: structuredClone(document.assembly.members[0].interfaces[0]) }];
  return document;
}

test('五金目录逐项覆盖当前接口 ID、中文显示名和默认可连接列表', () => {
  assert.equal(catalog.formatVersion, 1);
  assert.deepEqual(catalog.interfaceTypes.map(row => ({
    id: row.connectionTypeId, label: row.label, defaultConnectableTypeIds: row.connectableTypeIds,
  })), PRESET_INTERFACE_TYPES);
  const backendSource = readFileSync(new URL('../backend/app/components/service.py', import.meta.url), 'utf8');
  const backendIds = [...backendSource.match(/PRESET_INTERFACE_TYPE_IDS = \{([\s\S]*?)\}/)[1].matchAll(/"([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(backendIds.sort(), catalog.interfaceTypes.map(row => row.connectionTypeId).sort());
  assert.deepEqual(catalog.fallbacks.map(row => row.match), ['custom:*', null, 'unknown-nonempty-id']);
});

test('五金目录覆盖界面全部连接方式及组件模型自定义方式', () => {
  const ui = readFileSync(new URL('../src/stages/DefinitionStage.tsx', import.meta.url), 'utf8');
  const values = [...ui.match(/const connectionOptions: ConnectionType\[\] = \[([^\]]+)\]/)[1].matchAll(/"([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(catalog.connectionForms.filter(row => row.mode === 'preset').map(row => row.value), values);
  const model = readFileSync(new URL('../src/domain/component.ts', import.meta.url), 'utf8');
  const types = [...model.match(/export type ConnectionType = ([^;]+);/)[1].matchAll(/"([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(catalog.connectionForms.map(row => row.value).sort(), types.sort());
  const text = readFileSync(new URL('../docs/INTERFACE-HARDWARE.md', import.meta.url), 'utf8');
  for (const row of catalog.interfaceTypes) assert.ok(text.includes(row.connectionTypeId) && text.includes(row.label));
});

test('自动显示示例与实际推荐函数一致，包括定位销现有 M 前缀', () => {
  for (const row of catalog.interfaceTypes) {
    const actual = recommendInterfaceHardware({ id: 'sample', name: row.label, type: 'axis', tags: [],
      connectionTypeId: row.connectionTypeId, interfaceSize: catalog.automaticExampleContext.interfaceSize });
    assert.deepEqual(row.automaticHardwareAtDiameter8, actual, row.connectionTypeId);
  }
  assert.equal(catalog.interfaceTypes.find(row => row.connectionTypeId === 'pin.v1').automaticHardwareAtDiameter8[0].specification, 'M8');
});

test('所有方案引用存在；展开后仅含平台原生字段且可由实际 reducer 保存', () => {
  const profileIds = new Set(catalog.profiles.map(profile => profile.id));
  assert.equal(profileIds.size, catalog.profiles.length);
  for (const row of [...catalog.interfaceTypes, ...catalog.connectionForms, ...catalog.fallbacks]) {
    for (const id of row.profileIds) assert.ok(profileIds.has(id), id);
  }
  for (const profile of catalog.profiles) {
    const parameters = Object.fromEntries(profile.parameters.map(key => [key, '实际测试规格']));
    const request = buildHardwareRequest(draft(), catalog, { ...options, profileId: profile.id, parameters });
    assert.deepEqual(Object.keys(request).sort(), ['action', 'expectedRevision']);
    assert.deepEqual(Object.keys(request.action).sort(), ['hardware', 'interfaceId', 'memberId', 'type']);
    assert.deepEqual(Object.keys(request.action.hardware).sort(), ['items', 'mode']);
    assert.ok(!/[{}]/.test(request.action.hardware.items.map(row => row.specification).join('')));
    const original = draft();
    const updated = componentReducer(original, request.action);
    assert.notEqual(updated, original, profile.id);
    assert.deepEqual(updated.assembly.members[0].interfaces[0].hardware, request.action.hardware);
    assert.deepEqual(updated.ports[0].interface.hardware, request.action.hardware);
    assert.deepEqual(updated.assembly.members[1], original.assembly.members[1]);
  }
});

test('读取内部草稿、API 响应和实际导出格式均保留真实 ID 与修订号，不改输入', () => {
  const original = draft(), before = structuredClone(original);
  const request = buildHardwareRequest(original, catalog, options);
  assert.equal(request.expectedRevision, 7);
  assert.equal(request.action.memberId, 'a');
  assert.equal(request.action.interfaceId, 'endpoint');
  assert.deepEqual(buildHardwareRequest({ draftId: 'draft-id', document: original }, catalog, options), request);
  assert.deepEqual(buildHardwareRequest(JSON.parse(serializeComponent(original)), catalog, options), request);
  assert.deepEqual(original, before);
});

test('手动清单参数展开，保存与现有统计共用节点去重保持一致', () => {
  const request = buildHardwareRequest(draft(), catalog, { ...options, profileId: 'bolt-kit',
    parameters: { boltSpec: 'M8 × 30', nutSpec: 'M8', washerSpec: 'M8' } });
  let updated = componentReducer(draft(), request.action);
  updated = componentReducer(updated, { ...request.action, memberId: 'b' });
  const usage = summarizeHardwareUsage(updated.assembly.members, updated.assembly.mates);
  assert.equal(usage.reduce((sum, row) => sum + row.quantity, 0), 4);
  assert.equal(usage.find(row => row.category === '螺栓').specification, 'M8 × 30');
  updated = componentReducer(updated, { ...request.action, hardware: { mode: 'none', items: [] } });
  assert.equal(summarizeHardwareUsage(updated.assembly.members, updated.assembly.mates).reduce((sum, row) => sum + row.quantity, 0), 4);
  updated = componentReducer(updated, { ...request.action, memberId: 'b', hardware: { mode: 'none', items: [] } });
  assert.deepEqual(summarizeHardwareUsage(updated.assembly.members, updated.assembly.mates), []);
});

test('拒绝猜测规格、未解析占位符、错参和不符合原生模型的清单', () => {
  assert.throws(() => compileHardwareProfile(catalog, 'dowel-pin'), /pinSpec/);
  assert.throws(() => compileHardwareProfile(catalog, 'missing'), /不存在/);
  assert.throws(() => compileHardwareProfile(catalog, 'auto', { unused: 'x' }), /不使用参数/);
  assert.throws(() => compileHardwareProfile(catalog, 'dowel-pin', { pinSpec: '  ' }), /缺少/);
  assert.throws(() => compileHardwareProfile(catalog, 'dowel-pin', { pinSpec: '{diameter}' }), /占位符/);
  assert.throws(() => compileHardwareProfile(catalog, 'dowel-pin', { pinSpec: 'x'.repeat(121) }), /超出/);
  for (const quantity of [0, -1, 1.5, 10001, NaN]) {
    const bad = structuredClone(catalog);
    bad.profiles.find(profile => profile.id === 'dowel-pin').hardware.items[0].quantity = quantity;
    assert.throws(() => compileHardwareProfile(bad, 'dowel-pin', { pinSpec: '销规格' }), /超出/);
  }
});

test('拒绝失效、重复端点或无效修订；未知类型仍可明确手动配置', () => {
  assert.throws(() => buildHardwareRequest(draft(), catalog, { ...options, memberId: 'missing' }), /成员不存在/);
  assert.throws(() => buildHardwareRequest(draft(), catalog, { ...options, interfaceId: 'missing' }), /接口不存在/);
  for (const revision of [0, 1.5, '7', Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => buildHardwareRequest({ ...draft(), revision }, catalog, options), /revision/);
  }
  const duplicate = draft();
  duplicate.assembly.members.push(structuredClone(duplicate.assembly.members[0]));
  assert.throws(() => buildHardwareRequest(duplicate, catalog, options), /ID 不唯一/);
  const unknown = draft();
  unknown.assembly.members[0].interfaces[0].connectionTypeId = 'vendor:unknown';
  const request = buildHardwareRequest(unknown, catalog, { ...options, profileId: 'custom-item',
    parameters: { category: '连接件', name: '定制连接件', specification: '按真实图号' } });
  assert.equal(request.action.hardware.mode, 'manual');
  assert.equal(unknown.assembly.members[0].interfaces[0].connectionTypeId, 'vendor:unknown');
});

test('命令行可列出方案与帮助，缺少草稿参数时不生成请求', () => {
  const script = new URL('../scripts/prepare-interface-hardware.mjs', import.meta.url);
  const { pathname } = script;
  const scriptPath = process.platform === 'win32' ? decodeURIComponent(pathname.slice(1)) : decodeURIComponent(pathname);
  const list = spawnSync(process.execPath, [scriptPath, '--list'], { encoding: 'utf8' });
  assert.equal(list.status, 0, list.stderr);
  assert.equal(JSON.parse(list.stdout).length, catalog.profiles.length);
  const help = spawnSync(process.execPath, [scriptPath, '--help'], { encoding: 'utf8' });
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /--draft/);
  const missing = spawnSync(process.execPath, [scriptPath, '--profile', 'auto'], { encoding: 'utf8' });
  assert.equal(missing.status, 1);
  assert.equal(missing.stdout, '');
});

test('离线请求保存场景和说明，不按接口类型或尺寸限制选择', () => {
  const request = buildHardwareRequest(draft(), catalog, { ...options, profileId: 'none', scenarioId: 'weld-only', note: '焊接固定，无额外五金' });
  assert.equal(request.action.hardware.scenarioId, 'weld-only');
  assert.equal(request.action.hardware.note, '焊接固定，无额外五金');
  assert.equal(componentReducer(draft(), request.action).assembly.members[0].interfaces[0].hardware.scenarioId, 'weld-only');
  assert.throws(() => buildHardwareRequest(draft(), catalog, { ...options, scenarioId: 'missing' }), /不存在/);
  assert.throws(() => buildHardwareRequest(draft(), catalog, { ...options, scenarioId: 'weld-only' }), /模式不一致/);
});
