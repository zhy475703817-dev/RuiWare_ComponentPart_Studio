import test from 'node:test';
import assert from 'node:assert/strict';
import { memberParameterPatch } from '../src/domain/memberParameterEdit.ts';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { resolveComponentComposition } from '../src/domain/composition.ts';

const definition = (id, valueType, minimum = null, maximum = null) => ({ id, label: id, unit: 'mm', valueType, minimum, maximum, default: 0 });
const member = () => ({ id: 'a', name: 'a', code: 'a', role: 'a', position: [0, 0, 0], rotation: [0, 0, 0], interfaces: [], template: {}, parameterValues: { length: 1200, count: 2, text: 'old', flag: false }, parameterDefinitions: [definition('length', 'number', 100, 6000), definition('count', 'integer', 1, 10), definition('text', 'string'), definition('flag', 'boolean')] });

test('成员参数编辑只更新选定成员和参数，保留其他值', () => {
  const a = member();
  const document = createComponentDocument();
  document.assembly.members = [a, { ...structuredClone(a), id: 'b' }];
  const patch = memberParameterPatch(a, [], 'length', 2500);
  const next = componentReducer(document, { type: 'member.patch', id: 'a', patch });
  assert.equal(resolveComponentComposition(next).find(m => m.id === 'a').parameterValues.length, 2500);
  assert.equal(next.assembly.members[1].parameterValues.length, 1200);
  assert.equal(next.assembly.members[0].parameterValues.count, 2);
  assert.equal(a.parameterValues.length, 1200);
});

test('成员参数类型及范围校验，绑定参数需先显式解除', () => {
  const a = member();
  const bindings = [{ id: 'binding', memberId: 'a', memberParameterId: 'length', parameterId: 'componentLength' }];
  assert.equal(memberParameterPatch(a, bindings, 'length', 2500), null);
  for (const value of [NaN, Infinity, 0, 6001, '2500']) assert.equal(memberParameterPatch(a, [], 'length', value), null);
  assert.equal(memberParameterPatch(a, [], 'count', 1.5), null);
  assert.equal(memberParameterPatch(a, [], 'missing', 3), null);
  assert.equal(memberParameterPatch(a, [], 'flag', 'true'), null);
  assert.equal(memberParameterPatch(a, [], 'flag', true).parameterValues.flag, true);
  assert.equal(memberParameterPatch(a, [], 'text', 'new').parameterValues.text, 'new');
  assert.equal(memberParameterPatch(a, [], 'length', 100).parameterValues.length, 100);
  assert.equal(memberParameterPatch(a, [], 'length', 6000).parameterValues.length, 6000);
});
