import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';

test('模型撤回恢复模型与关联数据，保留定义及参数编辑', () => {
  const document = createComponentDocument();
  const model = structuredClone(Object.fromEntries(['assembly', 'ports', 'bindings', 'patterns', 'slots'].map(key => [key, document[key]])));
  document.definition.name = '后续名称';
  document.parameters[0].value = 4321;
  document.assembly.members.push({ id: 'added' });
  const restored = componentReducer(document, { type: 'model.restore', model });
  assert.equal(restored.assembly.members.length, 0);
  assert.equal(restored.definition.name, '后续名称');
  assert.equal(restored.parameters[0].value, 4321);
  assert.equal(restored.revision, document.revision + 1);
});
