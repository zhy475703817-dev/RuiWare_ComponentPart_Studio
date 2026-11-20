import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { parameterDefault } from '../src/domain/parameterDefaults.ts';

test('自定义参数默认值在修改、序列化和初始化后保持创建数值', () => {
  let document = componentReducer(createComponentDocument(), { type: 'parameter.add', parameter: { id: 'gap', label: '间隙', unit: 'mm', value: 42, min: 0, max: 100 } });
  document = componentReducer(document, { type: 'parameter.value', id: 'gap', value: 75 });
  document = JSON.parse(JSON.stringify(document));
  const parameter = document.parameters.find(p => p.id === 'gap');
  assert.equal(parameterDefault(parameter), 42);
  document = componentReducer(document, { type: 'parameter.value', id: 'gap', value: parameterDefault(parameter) });
  assert.equal(document.parameters.find(p => p.id === 'gap').value, 42);
  assert.equal(document.parameters[0].value, 2400);
});

test('内置参数恢复创建默认值，旧自定义参数首次修改前记录基准', () => {
  let document = createComponentDocument();
  document.parameters[0].value = 6000;
  assert.equal(parameterDefault(document.parameters[0]), 2400);
  document.parameters.push({ id: 'old', label: '旧参数', unit: 'mm', value: 12, min: 0, max: 100 });
  document = componentReducer(document, { type: 'parameter.value', id: 'old', value: 80 });
  assert.equal(parameterDefault(document.parameters.find(p => p.id === 'old')), 12);
});
