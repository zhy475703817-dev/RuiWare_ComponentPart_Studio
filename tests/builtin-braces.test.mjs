import assert from 'node:assert/strict';
import test from 'node:test';
import { createComponentDocument } from '../src/domain/component.ts';
import { hasBuiltInBraces } from '../src/domain/bracePolicy.ts';
import { validateComponent } from '../src/domain/validation.ts';

test('柱片在制作阶段必须自带规则，普通组件不受影响', () => {
  const doc = createComponentDocument();
  assert.equal(validateComponent(doc).some(c => c.id === 'brace.builtin'), false);
  doc.definition.name = '柱片';
  assert.equal(hasBuiltInBraces(doc), false);
  assert.equal(validateComponent(doc).find(c => c.id === 'brace.builtin').status, 'fail');
  doc.braceGenerationRules = [{ id: 'built-in', enabled: false, leftColumnMemberId: 'left', rightColumnMemberId: 'right',
    horizontalMemberId: 'cross', diagonalMemberId: 'diagonal', heightParameterId: 'componentHeight' }];
  doc.bindings = ['left', 'right'].map(memberId => ({ id: memberId, memberId, parameterId: 'componentHeight', memberParameterId: 'length' }));
  assert.equal(hasBuiltInBraces(doc), true);
  doc.bindings.pop();
  assert.equal(hasBuiltInBraces(doc), false);
});

test('相同结构换名称仍须内置规则，额外撑杆须明确归属', () => {
  const doc = createComponentDocument();
  const member = (id, role) => ({ id, role, name: role, template: {}, interfaces: [] });
  doc.assembly.members = [member('left', '立柱'), member('right', '立柱'), member('cross', '横撑'), member('diagonal', '斜撑')];
  assert.equal(hasBuiltInBraces(doc), false);
  doc.braceGenerationRules = [{ id: 'builtin', leftColumnMemberId: 'left', rightColumnMemberId: 'right',
    horizontalMemberId: 'cross', diagonalMemberId: 'diagonal', heightParameterId: 'componentHeight' }];
  doc.bindings = ['left', 'right'].map(memberId => ({ id: memberId, memberId, parameterId: 'componentHeight', memberParameterId: 'length' }));
  assert.equal(hasBuiltInBraces(doc), true);
  const extra = member('extra', '横撑');
  doc.assembly.members.push(extra);
  assert.equal(hasBuiltInBraces(doc), false);
  extra.generatedBy = 'builtin';
  assert.equal(hasBuiltInBraces(doc), true);
});
