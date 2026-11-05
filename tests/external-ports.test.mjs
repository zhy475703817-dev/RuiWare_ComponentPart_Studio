import assert from 'node:assert/strict';
import test from 'node:test';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { resolvePort } from '../src/domain/ports.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { Euler, Matrix4 } from 'three';

const member = { id: 'a', name: 'beam', code: '', role: 'beam', template: { publicationId: 'pub', templateId: 't', version: 2, sha256: 'a'.repeat(64) }, position: [100, 20, 0], rotation: [0, 0, Math.PI / 2], parameterValues: {}, interfaces: [{ id: 'end', name: 'end', type: 'plane', tags: ['beam'], frame: { origin: [10, 0, 0], rotation: [0, 0, 0] } }, { id: 'other', name: 'other', type: 'point', tags: [] }] };
const port = { id: 'p', name: 'external', source: { memberId: 'a', interfaceId: 'end' }, tags: ['beam'], required: true, compatibleTags: ['column'], allowedRelations: ['固定'] };
function draft() {
  const result = createComponentDocument();
  result.assembly.members = [structuredClone(member)];
  result.ports = [structuredClone(port)];
  return result;
}

test('外部坐标由成员与接口组合，移动和旋转后实时更新，不复制坐标', () => {
  let data = draft();
  let resolved = resolvePort(data, data.ports[0]);
  assert.equal(resolved.status, 'ready');
  assert.ok(Math.abs(resolved.frame.origin[0] - 100) < 1e-9);
  assert.ok(Math.abs(resolved.frame.origin[1] - 30) < 1e-9);
  assert.ok(Math.abs(resolved.frame.rotation[2] - Math.PI / 2) < 1e-9);
  data = componentReducer(data, { type: 'member.patch', id: 'a', patch: { position: [0, 0, 0], rotation: [0, 0, 0] } });
  assert.deepEqual(resolvePort(data, data.ports[0]).frame.origin, [10, 0, 0]);
  assert.equal('frame' in data.ports[0], false);
  const exported = JSON.parse(serializeComponent(data));
  assert.deepEqual(exported.stages.ports[0].compatibleTags, ['column']);
  assert.equal(exported.stages.portDiagnostics[0].sourceVersion.version, 2);
});

test('旧接口、未校准、失效引用与重复来源有明确问题码，绝不猜测原点', () => {
  const data = draft();
  delete data.assembly.members[0].interfaces[0].frame;
  delete data.ports[0].compatibleTags;
  delete data.ports[0].allowedRelations;
  let result = resolvePort(data, data.ports[0]);
  assert.equal(result.frame, null);
  assert.deepEqual(result.issues.map(item => item.code), ['FRAME_REQUIRED', 'COMPATIBILITY_REQUIRED', 'RELATIONS_REQUIRED']);
  data.ports.push({ ...data.ports[0], id: 'p2' });
  assert.ok(resolvePort(data, data.ports[0]).issues.some(item => item.code === 'DUPLICATE_SOURCE'));
  data.ports[0].source = { memberId: 'missing', interfaceId: 'end' };
  assert.equal(resolvePort(data, data.ports[0]).issues[0].code, 'SOURCE_MISSING');
});

test('接近或处于90度的方向仍与旋转矩阵保持精度一致', () => {
  for (const y of [Math.PI / 2 - .0001, Math.PI / 2, -Math.PI / 2]) {
    const data = draft();
    data.assembly.members[0].rotation = [.4, y, -.6];
    const resolved = resolvePort(data, structuredClone(data.ports[0]));
    assert.equal(resolved.status, 'ready');
    const expected = new Matrix4().makeRotationFromEuler(new Euler(.4, y, -.6));
    const actual = new Matrix4().makeRotationFromEuler(new Euler(...resolved.frame.rotation));
    actual.elements.forEach((value, index) => assert.ok(Math.abs(value - expected.elements[index]) < 1e-8));
  }
});

test('编辑保留稳定ID，拒绝重复或失效来源，取消暴露保留源零件与槽位', () => {
  let data = draft();
  const original = structuredClone(data);
  data = componentReducer(data, { type: 'port.patch', id: 'p', patch: { name: 'left', required: false } });
  assert.equal(data.ports[0].id, 'p');
  assert.equal(data.ports[0].name, 'left');
  assert.equal(original.ports[0].name, 'external');
  assert.equal(componentReducer(data, { type: 'port.patch', id: 'p', patch: { source: { memberId: 'missing', interfaceId: 'end' } } }), data);
  data = componentReducer(data, { type: 'port.add', port: { ...port, id: 'p2', source: { memberId: 'a', interfaceId: 'other' } } });
  assert.equal(componentReducer(data, { type: 'port.patch', id: 'p', patch: { source: { memberId: 'a', interfaceId: 'other' } } }), data);
  const removed = componentReducer(data, { type: 'port.remove', id: 'p' });
  assert.equal(removed.ports.length, 1);
  assert.deepEqual(removed.assembly, original.assembly);
  assert.deepEqual(removed.slots, original.slots);
});
