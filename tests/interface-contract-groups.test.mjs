import test from 'node:test';
import assert from 'node:assert/strict';
import { interfaceContractGroups } from '../src/domain/interfaceContractGroups.ts';

test('接口按契约族、角色、方式和版本分类，未定义契约单列且不修改原接口', () => {
  const a = { id: 'a', mating: { family: 'pin-hole', role: 'pin', connectionMethod: 'clearance-fit', version: 1 } };
  const b = { ...a, id: 'b' };
  const c = { id: 'c', mating: { ...a.mating, role: 'hole' } };
  const d = { id: 'd' };
  const groups = interfaceContractGroups([a, b, c, d]);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups[0].interfaces.map(i => i.id), ['a', 'b']);
  assert.equal(groups[0].interfaces[0], a);
  assert.match(groups[0].label, /销孔/);
  assert.equal(groups[2].label, '未定义配合契约');
  assert.deepEqual(interfaceContractGroups([]), []);
});
