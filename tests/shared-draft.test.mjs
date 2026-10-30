import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { validateComponent } from '../src/domain/validation.ts';
import { SharedDraftController } from '../src/app/sharedDraft.ts';

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const envelope = (draftId, document) => ({ draftId, document, checks: validateComponent(document) });
function server() {
  const documents = new Map();
  let serial = 0;
  const api = {
    async list() { return { items: [...documents].map(([draftId, d]) => ({ draftId, name: d.definition.name, revision: d.revision, updatedAt: '' })) }; },
    async create() { const id = `draft-${++serial}`; documents.set(id, createComponentDocument()); return envelope(id, structuredClone(documents.get(id))); },
    async get(id) { if (!documents.has(id)) throw Object.assign(new Error('missing'), { code: 'DRAFT_NOT_FOUND' }); return envelope(id, structuredClone(documents.get(id))); },
    async apply(id, revision, action) {
      const doc = documents.get(id);
      if (doc.revision !== revision) throw Object.assign(new Error('conflict'), { code: 'REVISION_CONFLICT', current: await api.get(id) });
      documents.set(id, componentReducer(doc, action));
      return api.get(id);
    },
    async export(id) { return { name: documents.get(id).definition.name }; },
  };
  return { api, documents };
}

test('快速输入立即显示，按服务端修订串行保存，不丢失后续输入', async () => {
  const { api, documents } = server();
  const gate = deferred();
  const apply = api.apply;
  api.apply = async (...args) => { await gate.promise; return apply(...args); };
  const workspace = new SharedDraftController(api);
  await workspace.start();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '横' } });
  workspace.dispatch({ type: 'definition.patch', patch: { name: '横梁' } });
  workspace.dispatch({ type: 'parameter.value', id: 'componentLength', value: 3200 });
  assert.equal(workspace.getSnapshot().document.definition.name, '横梁');
  assert.equal(workspace.getSnapshot().document.parameters[0].value, 3200);
  assert.equal(workspace.getSnapshot().dirty, true);
  gate.resolve();
  await workspace.flush();
  assert.equal(documents.get('draft-1').definition.name, '横梁');
  assert.equal(documents.get('draft-1').parameters[0].value, 3200);
  assert.equal(workspace.getSnapshot().document.revision, 4);
  assert.equal(workspace.getSnapshot().status, 'ready');
  assert.equal(workspace.getSnapshot().items.find(item => item.draftId === 'draft-1').name, '横梁');
  assert.equal(workspace.getSnapshot().items.find(item => item.draftId === 'draft-1').revision, 4);
});

test('轮询读取 AI 修改，编辑开始后迟到的轮询不能覆盖本地输入', async () => {
  const { api } = server();
  const workspace = new SharedDraftController(api);
  await workspace.start();
  await api.apply('draft-1', 1, { type: 'definition.patch', patch: { name: 'AI 修改' } });
  await workspace.poll();
  assert.equal(workspace.getSnapshot().document.definition.name, 'AI 修改');
  const gate = deferred();
  const get = api.get;
  api.get = async id => { const old = await get(id); await gate.promise; return old; };
  const polling = workspace.poll();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '网页新输入' } });
  assert.equal(workspace.getSnapshot().document.definition.name, '网页新输入');
  gate.resolve();
  await polling;
  await workspace.flush();
  assert.equal(workspace.getSnapshot().document.definition.name, '网页新输入');
});

test('修订冲突保留本地输入，冻结保存，显式重读后采用服务端草稿', async () => {
  const { api, documents } = server();
  const workspace = new SharedDraftController(api);
  await workspace.start();
  await api.apply('draft-1', 1, { type: 'definition.patch', patch: { name: 'AI 版本' } });
  workspace.dispatch({ type: 'definition.patch', patch: { name: '网页未保存' } });
  await assert.rejects(workspace.flush());
  assert.equal(workspace.getSnapshot().status, 'conflict');
  assert.equal(workspace.getSnapshot().document.definition.name, '网页未保存');
  await workspace.poll();
  assert.equal(workspace.getSnapshot().document.definition.name, '网页未保存');
  assert.equal(documents.get('draft-1').definition.name, 'AI 版本');
  await assert.rejects(workspace.create());
  await workspace.reload();
  assert.equal(workspace.getSnapshot().document.definition.name, 'AI 版本');
  assert.equal(workspace.getSnapshot().dirty, false);
});

test('网络失败保留待保存编辑，重试仍用原修订；响应丢失时不会重复覆盖', async () => {
  const { api, documents } = server();
  const apply = api.apply;
  let dropResponse = true;
  api.apply = async (...args) => {
    const result = await apply(...args);
    if (dropResponse) { dropResponse = false; throw new Error('网络中断'); }
    return result;
  };
  const workspace = new SharedDraftController(api);
  await workspace.start();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '已保存但响应丢失' } });
  await assert.rejects(workspace.flush());
  assert.equal(workspace.getSnapshot().dirty, true);
  await assert.rejects(workspace.retry());
  assert.equal(workspace.getSnapshot().status, 'conflict');
  assert.equal(documents.get('draft-1').revision, 2);
  await workspace.reload();
  assert.equal(workspace.getSnapshot().document.definition.name, '已保存但响应丢失');
});

test('切换草稿前保存，迟到的旧草稿读取不覆盖新选择', async () => {
  const { api, documents } = server();
  const workspace = new SharedDraftController(api);
  await workspace.start();
  const second = await api.create();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '旧草稿保存' } });
  await workspace.select(second.draftId);
  assert.equal(documents.get('draft-1').definition.name, '旧草稿保存');
  assert.equal(workspace.getSnapshot().draftId, 'draft-2');
  const gate = deferred();
  const get = api.get;
  api.get = async id => { const doc = await get(id); if (id === 'draft-1') await gate.promise; return doc; };
  const slow = workspace.select('draft-1');
  await workspace.select('draft-2');
  gate.resolve();
  await slow;
  assert.equal(workspace.getSnapshot().draftId, 'draft-2');
});

test('失效的保存标识回退到可用草稿；释放后迟到响应不会发布状态', async () => {
  const { api } = server();
  await api.create();
  const workspace = new SharedDraftController(api);
  await workspace.start('deleted-draft');
  assert.equal(workspace.getSnapshot().draftId, 'draft-1');
  assert.match(workspace.getSnapshot().notice, /不存在/);
  const gate = deferred();
  const get = api.get;
  api.get = async id => { await gate.promise; return get(id); };
  let updates = 0;
  workspace.subscribe(() => updates++);
  const polling = workspace.poll();
  workspace.dispose();
  const before = updates;
  gate.resolve();
  await polling;
  assert.equal(updates, before);
});

test('导出等待所有待保存操作，错误期间拒绝导出旧版本', async () => {
  const { api } = server();
  const workspace = new SharedDraftController(api);
  await workspace.start();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '最终名称' } });
  assert.deepEqual(await workspace.export(), { name: '最终名称' });
  api.apply = async () => { throw new Error('offline'); };
  workspace.dispatch({ type: 'definition.patch', patch: { name: '不能导出' } });
  await assert.rejects(workspace.export());
});

test('首次连接失败后重试仍打开链接指定的草稿，不切换到列表第一项', async () => {
  const { api } = server();
  await api.create();
  await api.create();
  const get = api.get;
  let offline = true;
  api.get = async id => { if (offline) throw new Error('offline'); return get(id); };
  const workspace = new SharedDraftController(api);
  await workspace.start('draft-2');
  assert.equal(workspace.getSnapshot().status, 'error');
  offline = false;
  await workspace.retry();
  assert.equal(workspace.getSnapshot().draftId, 'draft-2');
});

const agentChange = (revision, before, after) => ({
  actor: 'agent', fromRevision: revision - 1, revision, createdAt: '2026-10-20T08:00:00Z',
  items: [{ path: ['definition', 'name'], label: '组件定义 / 名称', kind: 'updated', before, after }],
});

test('空闲同步展示并累积 Agent 前后差异，同版本轮询不清除提示，切换草稿清除', async () => {
  const { api } = server();
  const get = api.get;
  let events = [];
  api.get = async (...args) => ({ ...await get(...args), changes: events });
  const workspace = new SharedDraftController(api);
  await workspace.start();
  await api.apply('draft-1', 1, { type: 'definition.patch', patch: { name: 'Agent 名称' } });
  events = [agentChange(2, '横梁组件', 'Agent 名称')];
  await workspace.poll();
  assert.equal(workspace.getSnapshot().agentChanges[0].items[0].before, '横梁组件');
  assert.equal(workspace.getSnapshot().document.definition.name, 'Agent 名称');
  await workspace.poll();
  assert.equal(workspace.getSnapshot().agentChanges.length, 1);
  await api.apply('draft-1', 2, { type: 'definition.patch', patch: { name: '再次修改' } });
  events.push(agentChange(3, 'Agent 名称', '再次修改'));
  await workspace.poll();
  assert.equal(workspace.getSnapshot().agentChanges.length, 2);
  workspace.dismissAgentChanges();
  assert.deepEqual(workspace.getSnapshot().agentChanges, []);
  await workspace.poll();
  assert.deepEqual(workspace.getSnapshot().agentChanges, []);
  const second = await api.create();
  await workspace.select(second.draftId);
  assert.deepEqual(workspace.getSnapshot().agentChanges, []);
});

test('保存响应包含 Agent 中间修订时展示差异，并明确标记用户优先', async () => {
  const { api } = server();
  const apply = api.apply;
  api.apply = async (...args) => ({ ...await apply(...args), reconciled: true,
    changes: [agentChange(2, '横梁组件', 'Agent 名称')] });
  const workspace = new SharedDraftController(api);
  await workspace.start();
  workspace.dispatch({ type: 'definition.patch', patch: { name: '用户名称' } });
  await workspace.flush();
  assert.equal(workspace.getSnapshot().document.definition.name, '用户名称');
  assert.equal(workspace.getSnapshot().userPriority, true);
  assert.equal(workspace.getSnapshot().agentChanges.length, 1);
  assert.equal(workspace.getSnapshot().dirty, false);
});

test('其他网页修改不会冒充 Agent，结构删除冲突仍保留本地输入和差异', async () => {
  const { api } = server();
  const get = api.get;
  api.get = async (...args) => ({ ...await get(...args), changes: [{ ...agentChange(2, '横梁组件', '其他网页'), actor: 'user' }] });
  const workspace = new SharedDraftController(api);
  await workspace.start();
  await api.apply('draft-1', 1, { type: 'definition.patch', patch: { name: '其他网页' } });
  await workspace.poll();
  assert.deepEqual(workspace.getSnapshot().agentChanges, []);
  api.apply = async () => { throw Object.assign(new Error('removed'), { code: 'EDIT_TARGET_REMOVED',
    current: { ...await get('draft-1'), changes: [agentChange(3, '其他网页', 'Agent 修改')] } }); };
  workspace.dispatch({ type: 'definition.patch', patch: { name: '用户输入保留' } });
  await assert.rejects(workspace.flush());
  assert.equal(workspace.getSnapshot().status, 'conflict');
  assert.equal(workspace.getSnapshot().document.definition.name, '用户输入保留');
  assert.match(workspace.getSnapshot().error, /删除/);
  assert.equal(workspace.getSnapshot().agentChanges.length, 1);
});

test('先前保存成功但 Agent 删除后续排队操作的引用时，保留完整本地投影供备份', async () => {
  const { api, documents } = server();
  const created = await api.create();
  documents.get(created.draftId).assembly.members = [{ id: 'a', name: '零部件', code: 'A', role: '横梁',
    template: { publicationId: 'v1', templateId: 'p1', version: 1, sha256: 'a'.repeat(64) },
    position: [0,0,0], rotation: [0,0,0], parameterValues: {}, interfaces: [{ id: 'face', name: '端面', type: 'plane', tags: [] }] }];
  const gate = deferred();
  let calls = 0;
  api.apply = async () => {
    if (++calls > 1) throw Object.assign(new Error('invalid reference'), { code: 'INVALID_REFERENCE' });
    await gate.promise;
    let remote = componentReducer(documents.get(created.draftId), { type: 'member.remove', id: 'a' });
    remote = componentReducer(remote, { type: 'definition.patch', patch: { name: '用户名称' } });
    documents.set(created.draftId, remote);
    return { ...envelope(created.draftId, structuredClone(remote)), reconciled: true, changes: [agentChange(2, '旧', '新')] };
  };
  const workspace = new SharedDraftController(api);
  await workspace.start(created.draftId);
  workspace.dispatch({ type: 'definition.patch', patch: { name: '用户名称' } });
  workspace.dispatch({ type: 'port.add', port: { id: 'user-port', name: '用户接口', source: { memberId: 'a', interfaceId: 'face' }, tags: [] } });
  assert.equal(workspace.getSnapshot().document.ports[0].id, 'user-port');
  gate.resolve();
  await assert.rejects(workspace.flush());
  assert.equal(workspace.getSnapshot().status, 'conflict');
  assert.equal(workspace.getSnapshot().dirty, true);
  assert.equal(workspace.getSnapshot().document.ports[0].id, 'user-port');
  assert.equal(workspace.getSnapshot().document.assembly.members[0].id, 'a');
  assert.equal(workspace.getSnapshot().document.definition.name, '用户名称');
  await workspace.reload();
  assert.deepEqual(workspace.getSnapshot().document.assembly.members, []);
  assert.equal(workspace.getSnapshot().dirty, false);
});
