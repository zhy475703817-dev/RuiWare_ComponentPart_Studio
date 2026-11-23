import test from 'node:test';
import assert from 'node:assert/strict';
import { solveConnectedFaces } from '../src/domain/connectionSolver.ts';
const face = (id, ref, region) => ({ id, name: id, type: 'plane', tags: [], geometryRefs: [ref], ...(region ? { region } : {}) });
const member = (id, position = [0, 0, 0]) => ({ id, position, rotation: [0, 0, 0], interfaces: [face('start', 'part.endFace.start'), face('end', 'part.endFace.end')] });
const mate = (a, b) => ({ id: a + b, from: { memberId: a, interfaceId: 'end' }, to: { memberId: b, interfaceId: 'start' }, relation: '贴合', offset: 0 });
const box = length => ({ min: [-10, -10, 0], max: [10, 10, length] });

test('长度变化使用新模型连接面，传播到连接链，保留固定端与输入数据', () => {
  const members = [member('a', [20, 30, 0]), member('b'), member('c')];
  const mates = [mate('a', 'b'), mate('b', 'c')];
  const initial = solveConnectedFaces(members, mates, new Map([['a', box(100)], ['b', box(50)], ['c', box(40)]]));
  assert.deepEqual(initial.map(m => m.position), [[20, 30, 0], [20, 30, 100], [20, 30, 150]]);
  const changed = solveConnectedFaces(initial, mates, new Map([['a', box(200)], ['b', box(80)], ['c', box(40)]]));
  assert.deepEqual(changed.map(m => m.position), [[20, 30, 0], [20, 30, 200], [20, 30, 280]]);
  assert.deepEqual(members[1].position, [0, 0, 0]);
});

test('派生接口区域变化后按新中心贴合，不复用旧区域', () => {
  const a = member('a'), b = member('b');
  a.interfaces[1] = face('end', 'part.endFace.end', { mode: 'rectangle', uStart: 8, vStart: 3, uSpan: 2, vSpan: 2 });
  const [fixed, moved] = solveConnectedFaces([a, b], [mate('a', 'b')], new Map([['a', box(100)], ['b', box(50)]]));
  assert.deepEqual(moved.position, [8, 3, 100]);
  fixed.interfaces[1].region.uStart = 4;
  assert.deepEqual(solveConnectedFaces([fixed, moved], [mate('a', 'b')], new Map([['a', box(100)], ['b', box(50)]]))[1].position, [4, 3, 100]);
});

test('参数变化导致接口消失或闭环冲突时明确拒绝，不伪报贴合', () => {
  const a = member('a'), b = member('b');
  b.interfaces = [];
  assert.throws(() => solveConnectedFaces([a, b], [mate('a', 'b')], new Map([['a', box(100)], ['b', box(50)]])), /接口不存在/);
  assert.throws(() => solveConnectedFaces([member('a'), member('b')], [mate('a', 'b'), mate('b', 'a')], new Map([['a', box(100)], ['b', box(50)]])), /约束冲突/);
});
