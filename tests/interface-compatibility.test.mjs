import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { compatibleMemberIds, interfacesCompatible } from '../src/domain/interfaceCompatibility.ts';

const frame = { axis: 'z' };
const contract = (family, role, fields = {}) => ({
  version: 1, family, role, dimensions: {}, allowedRelations: family === 'plane-contact' ? ['贴合'] : ['同轴'], capacity: 1, ...fields,
});
const iface = (id, family, role, fields = {}) => ({
  id, name: id, type: family === 'plane-contact' ? 'plane' : 'axis', tags: [], interfaceType: 'connecting', referenceFrame: frame,
  mating: contract(family, role, fields.mating), ...fields, mating: contract(family, role, fields.mating),
});
const diameter = (nominal, lowerTolerance = 0, upperTolerance = 0, parameterRef = null) => ({ nominal, lowerTolerance, upperTolerance, unit: 'mm', parameterRef });
const member = (id, interfaces) => ({ id, role: id, parameterValues: {}, parameterDefinitions: [], interfaces });

test('shared compatibility vectors preserve status, reason codes, and rule version', () => {
  const path = fileURLToPath(new URL('./fixtures/interface-compatibility-v1.json', import.meta.url));
  const vectors = JSON.parse(readFileSync(path, 'utf8'));
  for (const vector of vectors.cases) {
    const result = interfacesCompatible(vector.a, vector.b, vector.relation);
    assert.equal(result.ruleVersion, vectors.ruleVersion, vector.name);
    assert.equal(result.status, vector.status, vector.name);
    assert.deepEqual(result.checks.filter(item => item.status !== 'pass').map(item => item.code).sort(), [...vector.reasonCodes].sort(), vector.name);
  }
});

test('缺少配合契约必须待复核，不能默认兼容', () => {
  const a = { id: 'a', name: 'a', type: 'axis', tags: [] };
  const b = { id: 'b', name: 'b', type: 'axis', tags: [] };
  const result = interfacesCompatible(a, b);
  assert.equal(result.status, 'needs_review');
  assert.equal(result.compatible, false);
  assert.equal(result.checks[0].code, 'MISSING_CONTRACT');
});

test('pin-hole 角色、尺寸、公差、机制和关系全部满足时兼容', () => {
  const pin = iface('pin', 'pin-hole', 'pin', { mating: { dimensions: { diameter: diameter(10) }, connectionMethod: 'clearance-fit' } });
  const hole = iface('hole', 'pin-hole', 'hole', { mating: { dimensions: { diameter: diameter(10.2) }, connectionMethod: 'clearance-fit' } });
  const result = interfacesCompatible(pin, hole);
  assert.equal(result.status, 'compatible');
  assert.equal(result.compatible, true);
});

test('pin-hole 角色相同、尺寸不满足或关系不允许时不兼容', () => {
  const pin = iface('pin', 'pin-hole', 'pin', { mating: { dimensions: { diameter: diameter(10) }, connectionMethod: 'clearance-fit' } });
  const sameRole = iface('pin2', 'pin-hole', 'pin', { mating: { dimensions: { diameter: diameter(10.2) }, connectionMethod: 'clearance-fit' } });
  assert.equal(interfacesCompatible(pin, sameRole).status, 'incompatible');
  const tightHole = iface('hole', 'pin-hole', 'hole', { mating: { dimensions: { diameter: diameter(9) }, connectionMethod: 'clearance-fit' } });
  assert.equal(interfacesCompatible(pin, tightHole).status, 'incompatible');
  const noRelation = iface('hole2', 'pin-hole', 'hole', { mating: { dimensions: { diameter: diameter(10.2) }, connectionMethod: 'clearance-fit', allowedRelations: [] } });
  assert.equal(interfacesCompatible(pin, noRelation, '同轴').status, 'incompatible');
});

test('plane-contact 要求平面几何、共同关系和有效参考系', () => {
  const a = iface('face-a', 'plane-contact', 'contact-face');
  const b = iface('face-b', 'plane-contact', 'contact-face');
  assert.equal(interfacesCompatible(a, b, '贴合').status, 'compatible');
  assert.equal(interfacesCompatible(a, { ...b, type: 'axis' }, '贴合').status, 'incompatible');
  assert.equal(interfacesCompatible(a, { ...b, mating: { ...b.mating, allowedRelations: ['重合'] } }, '贴合').status, 'incompatible');
  assert.equal(interfacesCompatible(a, { ...b, referenceFrame: undefined }).status, 'needs_review');
});

test('轴孔尺寸缺失、未知配合族和参数未解析时待复核', () => {
  const pin = iface('pin', 'pin-hole', 'pin', { mating: { connectionMethod: 'clearance-fit' } });
  const hole = iface('hole', 'pin-hole', 'hole', { mating: { connectionMethod: 'clearance-fit' } });
  assert.equal(interfacesCompatible(pin, hole).status, 'needs_review');
  const unknown = iface('a', 'custom-fit', 'custom', { mating: { family: 'custom-fit' } });
  assert.equal(interfacesCompatible(unknown, unknown).status, 'needs_review');
  const parameterPin = iface('parameter-pin', 'pin-hole', 'pin', { mating: { dimensions: { diameter: diameter(10, 0, 0, 'diameter') }, connectionMethod: 'clearance-fit' } });
  const parameterHole = iface('parameter-hole', 'pin-hole', 'hole', { mating: { dimensions: { diameter: diameter(10.2) }, connectionMethod: 'clearance-fit' } });
  assert.equal(interfacesCompatible(parameterPin, parameterHole).status, 'needs_review');
});

test('compatibleMemberIds 只返回已明确兼容的成员', () => {
  const source = iface('source', 'pin-hole', 'pin', { mating: { dimensions: { diameter: diameter(8) }, connectionMethod: 'clearance-fit' } });
  const compatible = iface('compatible', 'pin-hole', 'hole', { mating: { dimensions: { diameter: diameter(8.1) }, connectionMethod: 'clearance-fit' } });
  const missing = { id: 'missing', name: 'missing', type: 'axis', tags: [] };
  const members = [member('source', [source]), member('compatible', [compatible]), member('missing', [missing])];
  assert.deepEqual([...compatibleMemberIds(members, 'source')], ['compatible']);
});
