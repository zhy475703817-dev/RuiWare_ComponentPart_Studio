import test from 'node:test';
import assert from 'node:assert/strict';
import { parameterExpressions } from '../src/domain/parameterExpressions.ts';
import { resolveComponentComposition } from '../src/domain/composition.ts';

const length = { id: 'length', label: '组件总长', unit: 'mm', min: 10, max: 500, value: 120.5 };
const count = { id: 'count', label: '横撑数量', unit: '个', min: 1, max: 10, value: 3 };
const spacing = { id: 'spacing', label: '横撑间距', unit: 'mm', min: 0, max: 500, value: 80 };
const parameters = [length, count, spacing];
const member = {
  id: 'beam-1', name: '横撑', role: '横撑', code: '', interfaces: [],
  template: { publicationId: 'pub', templateId: 'beam', version: 1, sha256: '' },
  position: [10, 20, 30], rotation: [0, 0, 0], parameterValues: {},
  parameterDefinitions: [{ id: 'L', label: '长度', unit: 'mm', valueType: 'number', minimum: 50, maximum: 300, default: 100 }],
};
const binding = { id: 'b', parameterId: length.id, memberId: member.id, memberParameterId: 'L' };
const pattern = { id: 'array', label: '横撑阵列', memberIds: [member.id], countParameterId: count.id, spacingParameterId: spacing.id, axis: 'z' };
const describe = (parameter, bindings = [binding], patterns = [pattern], members = [member]) => parameterExpressions(parameter, parameters, bindings, members, patterns);

test('成员绑定表达式包含准确参数名、范围及与实际求解一致的当前数值', () => {
  const [row] = describe(length);
  assert.equal(row.expression, '横撑 · 长度（L） = clamp(组件总长（length）, 50, 300)');
  const resolved = resolveComponentComposition({ parameters, bindings: [binding], patterns: [], assembly: { members: [member], mates: [] } });
  assert.ok(row.note.includes(`当前：${resolved[0].parameterValues.L} mm`));
  assert.equal(describe({ ...length, value: 250 })[0].note.includes('当前：250 mm'), true);
  assert.deepEqual(describe(length, []), []);
});

test('整数绑定与空值说明符合取整、限幅和成员值回退行为', () => {
  const integerMember = { ...member, parameterDefinitions: [{ ...member.parameterDefinitions[0], valueType: 'integer' }] };
  const [row] = describe(length, [binding], [], [integerMember]);
  assert.ok(row.expression.includes('clamp(round(组件总长（length）), 50, 300)'));
  assert.ok(row.note.includes('当前：121 mm'));
  assert.ok(describe({ ...length, value: 600 })[0].note.includes('当前：300 mm'));
  assert.ok(describe({ ...length, value: null })[0].note.includes('当前：100 mm'));
  assert.ok(describe({ ...length, value: null }, [binding], [], [{ ...member, parameterValues: { L: 90 } }])[0].note.includes('当前：90 mm'));
});

test('无效、已移除或无关绑定不被列为生效关联', () => {
  assert.deepEqual(describe(length, [{ ...binding, memberId: 'deleted' }]), []);
  assert.deepEqual(describe(length, [{ ...binding, memberParameterId: 'deleted' }]), []);
  assert.deepEqual(describe(length, [binding], [], [{ ...member, parameterDefinitions: [{ ...member.parameterDefinitions[0], unit: 'kg' }] }]), []);
  assert.deepEqual(describe({ ...length, min: 400 }), []);
  assert.deepEqual(describe({ ...length, id: 'unrelated' }), []);
});

test('数量与间距参数分别展示同一阵列的关联表达式，随数值和配置更新', () => {
  for (const parameter of [count, spacing]) {
    const rows = describe(parameter);
    assert.equal(rows.length, 2);
    assert.ok(rows[0].expression.includes('max(1, round(横撑数量（count）))'));
    assert.ok(rows[1].expression.includes('Z 坐标 + i × 横撑间距（spacing）'));
    assert.ok(rows[0].note.includes('N = 3'));
    assert.ok(rows[1].note.includes('当前间距 = 80 mm'));
    assert.ok(rows[1].note.includes('横撑（beam-1）'));
  }
  const changed = parameterExpressions(count, [length, { ...count, value: 4.6 }, { ...spacing, value: 95 }], [], [member], [{ ...pattern, axis: 'x' }]);
  assert.ok(changed[0].note.includes('N = 5'));
  assert.ok(changed[1].expression.includes('X 坐标'));
  assert.ok(changed[1].note.includes('95 mm'));
  const unset = parameterExpressions(count, [{ ...count, value: null }, { ...spacing, value: null }], [], [member], [pattern]);
  assert.ok(unset[0].note.includes('N = 1'));
  assert.ok(unset[1].note.includes('当前间距 = 0 mm'));
  assert.deepEqual(describe(count, [], []), []);
  assert.deepEqual(describe(count, [], [pattern], []), []);
  assert.deepEqual(parameterExpressions(count, [count], [], [member], [pattern]), []);
});

test('派生指标显示实际包络体积与成员重量汇总公式', () => {
  assert.ok(describe({ ...length, id: 'volume', label: '体积' })[0].expression.includes('/ 10⁹'));
  assert.ok(describe({ ...length, id: 'mass', label: '重量' })[0].expression.includes('Σ 成员实例重量'));
});
