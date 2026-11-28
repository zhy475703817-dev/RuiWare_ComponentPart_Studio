import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { captureModelHistory, restoreModelHistory, canRestoreModelHistory } from '../src/domain/modelHistory.ts';

test('模型撤回恢复模型与关联数据，保留定义及参数编辑', () => {
  const document = createComponentDocument();
  const history = captureModelHistory(document);
  document.definition.name = '后续名称';
  document.parameters[0].value = 4321;
  document.assembly.members.push({ id: 'added' });
  const restored = componentReducer(document, { type: 'model.restore', model: restoreModelHistory(history, document) });
  assert.equal(restored.assembly.members.length, 0);
  assert.equal(restored.definition.name, '后续名称');
  assert.equal(restored.parameters[0].value, 4321);
  assert.equal(restored.revision, document.revision + 1);
});

test('撤回已发布模板时恢复自动尺寸和接口分类，保留后来编辑', () => {
  const before = createComponentDocument();
  before.interfaceTypes = [{ id: 'custom:保留', label: '保留' }];
  const member = {
    id: 'beam', name: '横梁', code: 'BEAM', role: '横梁',
    template: { publicationId: 'beam-1', templateId: 'beam', version: 1, sha256: 'a'.repeat(64) },
    position: [0, 0, 0], rotation: [0, 0, 0], parameterValues: { length: 3200, width: 900 },
    parameterDefinitions: [
      { id: 'length', label: '长度', unit: 'mm', valueType: 'number', default: 3200, minimum: null, maximum: null },
      { id: 'width', label: '宽度', unit: 'mm', valueType: 'number', default: 900, minimum: null, maximum: null },
    ],
    interfaces: [{ id: 'end', name: '连接面', type: 'plane', tags: [], connectionTypeId: 'custom:继承' }],
  };
  const after = [
    { type: 'interface.types.merge', types: [{ id: 'custom:继承', label: '继承' }] },
    { type: 'member.add', member },
  ].reduce(componentReducer, before);
  assert.deepEqual(after.parameters.slice(0, 2).map(parameter => parameter.value), [3200, 900]);
  const history = captureModelHistory(before, after);
  const current = [
    { type: 'parameter.value', id: 'componentWidth', value: 1250 },
    { type: 'interface.types.merge', types: [{ id: 'custom:后来', label: '后来' }] },
    { type: 'definition.patch', patch: { name: '后来名称' } },
  ].reduce(componentReducer, after);
  const restored = componentReducer(current, { type: 'model.restore', model: restoreModelHistory(history, current) });
  assert.deepEqual(restored.assembly.members, []);
  assert.deepEqual(restored.bindings, []);
  assert.deepEqual(restored.parameters.slice(0, 2).map(parameter => parameter.value), [2400, 1250]);
  assert.deepEqual(restored.interfaceTypes.map(type => type.id), ['custom:保留', 'custom:后来']);
  assert.equal(restored.definition.name, '后来名称');
});

test('撤回已发布模板时保留已有成员引用的分类', () => {
  const before = createComponentDocument();
  before.assembly.members = [{
    id: 'existing',
    interfaces: [{ id: 'end', name: '连接面', type: 'plane', tags: [], connectableTypeIds: ['custom:共用'] }],
  }];
  const after = componentReducer(before, {
    type: 'interface.types.merge', types: [{ id: 'custom:共用', label: '共用' }],
  });
  const model = restoreModelHistory(captureModelHistory(before, after), after);
  const restored = componentReducer(after, { type: 'model.restore', model });
  assert.deepEqual(restored.interfaceTypes, after.interfaceTypes);
});


test('后来独立成员修改使快照撤销失效，不能删除 Agent 新增成员', () => {
  const before=createComponentDocument();
  const after=structuredClone(before);
  after.assembly.members=[{id:'user-member',interfaces:[]}];
  const history=captureModelHistory(before,after);
  const current=structuredClone(after);
  current.assembly.members.push({id:'agent-member',interfaces:[]});
  assert.equal(canRestoreModelHistory(history,current),false);
  assert.equal(canRestoreModelHistory(history,after),true);
  after.definition.name='后续定义';
  assert.equal(canRestoreModelHistory(history,after),true);
});
