import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { SharedDraftController } from '../src/app/sharedDraft.ts';

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function setup() {
  let document = createComponentDocument();
  const envelope = () => ({ draftId: 'a', document: structuredClone(document), checks: [], operations: [] });
  const calls = [];
  const api = {
    list: async () => ({ items: [{ draftId: 'a', revision: document.revision, name: 'a' }] }),
    get: async () => envelope(),
    apply: async (id, revision, action, reason) => {
      calls.push({ id, revision, action, reason });
      if (revision !== document.revision) throw Object.assign(new Error('conflict'), { code: 'REVISION_CONFLICT' });
      document = componentReducer(document, action);
      return envelope();
    },
  };
  return { api, calls };
}

test('接口命令先保存排队输入，期间拒绝编辑和切换，传递精确修订与依据', async () => {
  const { api, calls } = setup();
  const controller = new SharedDraftController(api);
  await controller.start('a');
  await controller.flush();
  calls.length = 0;
  const gate = deferred();
  const apply = api.apply;
  api.apply = async (...args) => { await gate.promise; return apply(...args); };
  controller.dispatch({ type: 'definition.patch', patch: { name: 'saved first' } });
  const command = controller.executePortCommand({ type: 'port.remove', id: 'missing' }, undefined, '人工取消暴露');
  assert.equal(controller.getSnapshot().commandBusy, true);
  assert.equal(controller.dispatch({ type: 'definition.patch', patch: { name: 'blocked' } }), false);
  await assert.rejects(controller.select('b'), /接口命令/);
  await assert.rejects(controller.executePortCommand({ type: 'port.remove', id: 'missing' }), /命令/);
  gate.resolve();
  await command;
  assert.equal(calls[1].revision, calls[0].revision + 1);
  assert.equal(calls[1].reason, '人工取消暴露');
  assert.equal(controller.getSnapshot().document.definition.name, 'saved first');
  assert.equal(controller.getSnapshot().commandBusy, false);
});

test('失败命令不做乐观修改；修订冲突需重读，服务端校验失败仍可纠正', async () => {
  const { api } = setup();
  const controller = new SharedDraftController(api);
  await controller.start('a');
  await controller.flush();
  const before = structuredClone(controller.getSnapshot().document);
  api.apply = async () => { throw Object.assign(new Error('human required'), { code: 'HUMAN_CONFIRMATION_REQUIRED' }); };
  await assert.rejects(controller.executePortCommand({ type: 'port.confirm', id: 'p', scope: 'basis', note: 'x' }));
  assert.deepEqual(controller.getSnapshot().document, before);
  assert.equal(controller.getSnapshot().status, 'ready');
  api.apply = async () => { throw Object.assign(new Error('conflict'), { code: 'REVISION_CONFLICT' }); };
  await assert.rejects(controller.executePortCommand({ type: 'port.remove', id: 'p' }, before.revision));
  assert.equal(controller.getSnapshot().status, 'conflict');
  assert.equal(controller.getSnapshot().commandBusy, false);
  await controller.reload();
  assert.equal(controller.getSnapshot().status, 'ready');
});
