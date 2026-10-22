import assert from 'node:assert/strict';
import test from 'node:test';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { validateComponent } from '../src/domain/validation.ts';
import { serializeComponent } from '../src/domain/serialization.ts';

const template = { publicationId: 'version.1', templateId: 'part.1', version: 3, sha256: 'a'.repeat(64) };
const member = id => ({
  id, name: '横梁', code: 'BEAM', role: id, template: { ...template },
  position: [0, 0, 0], rotation: [0, 0, 0], parameterValues: { length: 2400 },
  interfaces: [{ id: 'face.left', name: '左端面', type: 'plane', tags: ['end-face'] }],
});
const source = memberId => ({ memberId, interfaceId: 'face.left' });

test('定义、参数及规则编辑共用一份不可变草稿并增加修订', () => {
  const original = createComponentDocument();
  let draft = componentReducer(original, { type: 'definition.patch', patch: { name: '连接组件', category: '连接组件', description: '说明' } });
  draft = componentReducer(draft, { type: 'parameter.value', id: 'componentLength', value: 3200 });
  draft = componentReducer(draft, { type: 'rule.expression', id: 'rule-1', expression: '长度限制' });
  assert.equal(original.definition.name, '横梁组件');
  assert.equal(original.parameters[0].value, 2400);
  assert.equal(draft.definition.description, '说明');
  assert.equal(draft.parameters[0].value, 3200);
  assert.equal(draft.rules[0].expression, '长度限制');
  assert.equal(draft.revision, original.revision + 3);
  assert.equal(componentReducer(draft, { type: 'parameter.value', id: 'missing', value: 10 }), draft);
  assert.deepEqual(componentReducer(draft, { type: 'reset' }), createComponentDocument());
});

test('同一发布版本可以添加多个成员，成员值不修改模板和原始输入', () => {
  const input = member('a');
  let draft = componentReducer(createComponentDocument(), { type: 'member.add', member: input });
  draft = componentReducer(draft, { type: 'member.add', member: member('b') });
  input.template.version = 99;
  draft = componentReducer(draft, { type: 'member.patch', id: 'a', patch: { position: [100, 0, 0], parameterValues: { length: 3000 } } });
  assert.equal(draft.assembly.members.length, 2);
  assert.deepEqual(draft.assembly.members[0].template, template);
  assert.equal(draft.assembly.members[1].parameterValues.length, 2400);
  assert.deepEqual(draft.assembly.members[0].position, [100, 0, 0]);
  assert.equal(componentReducer(draft, { type: 'member.add', member: member('a') }), draft);
});

test('删除成员清理连接、继承接口和绑定，保留其他成员的数据与槽位定义', () => {
  let draft = createComponentDocument();
  draft = { ...draft, assembly: { members: [member('a'), member('b'), member('c')], mates: [
    { id: 'ab', from: source('a'), to: source('b'), relation: '贴合', offset: 0 },
    { id: 'bc', from: source('b'), to: source('c'), relation: '贴合', offset: 0 },
  ] }, ports: [
    { id: 'pa', name: '接口 A', source: source('a'), tags: [] },
    { id: 'pb', name: '接口 B', source: source('b'), tags: [] },
  ], bindings: [
    { id: 'ba', parameterId: 'componentLength', memberId: 'a', memberParameterId: 'length' },
    { id: 'bb', parameterId: 'componentLength', memberId: 'b', memberParameterId: 'length' },
  ], slots: [{ id: 'slot', name: '横梁槽位', memberId: 'a', required: true }] };
  const result = componentReducer(draft, { type: 'member.remove', id: 'a' });
  assert.deepEqual(result.assembly.members.map(m => m.id), ['b', 'c']);
  assert.deepEqual(result.assembly.mates.map(m => m.id), ['bc']);
  assert.deepEqual(result.ports.map(p => p.id), ['pb']);
  assert.deepEqual(result.bindings.map(b => b.id), ['bb']);
  assert.equal(result.slots[0].memberId, null);
  assert.equal(draft.slots[0].memberId, 'a');
  assert.equal(result.revision, draft.revision + 1);
});

test('添加连接及继承接口使用结构化引用并拒绝不存在的来源', () => {
  let draft = createComponentDocument();
  draft.assembly.members = [member('member.a'), member('member.b')];
  const mate = { id: 'mate', from: source('member.a'), to: source('member.b'), relation: '贴合', offset: 0 };
  draft = componentReducer(draft, { type: 'mate.add', mate });
  assert.equal(draft.assembly.mates[0].from.interfaceId, 'face.left');
  assert.equal(componentReducer(draft, { type: 'mate.add', mate: { ...mate, id: 'invalid', to: source('missing') } }), draft);
  const port = { id: 'port', name: '外部接口', source: source('member.a'), tags: [] };
  draft = componentReducer(draft, { type: 'port.add', port });
  assert.equal(draft.ports.length, 1);
  assert.equal(componentReducer(draft, { type: 'port.add', port: { ...port, id: 'again' } }), draft);
});

test('基础验证检查固定版本、来源、绑定和数值，未执行能力不会显示通过', () => {
  let draft = createComponentDocument();
  draft.assembly.members = [member('a')];
  draft.ports = [{ id: 'p', name: '接口', source: source('a'), tags: [] }];
  let checks = validateComponent(draft);
  assert.equal(checks.find(c => c.id === 'references').status, 'pass');
  assert.equal(checks.find(c => c.id === 'rules.execution').status, 'pending');
  draft.parameters[0].value = null;
  draft.assembly.members[0].template.sha256 = 'bad';
  draft.ports[0].source.memberId = 'missing';
  draft.bindings = [{ id: 'b', parameterId: 'unknown', memberId: 'a', memberParameterId: 'length' }];
  checks = validateComponent(draft);
  for (const id of ['parameters', 'references', 'ports', 'bindings']) assert.equal(checks.find(c => c.id === id).status, 'fail');
});

test('导出保留原主体结构和完整版本引用，不包含页面或渲染状态', () => {
  const draft = createComponentDocument();
  draft.assembly.members = [member('a')];
  draft.definition.description = '导出说明';
  const result = JSON.parse(serializeComponent({ ...draft, selectedId: 'ui-only', previewTemplate: { secret: 'runtime-only' } }));
  assert.equal(result.schemaVersion, 'component-1.0');
  assert.equal(result.name, '横梁组件');
  assert.equal(result.stages.definition.description, '导出说明');
  assert.equal(result.stages.parameters.componentLength, 2400);
  assert.equal(result.stages.assembly.members[0].memberId, 'a');
  assert.equal(result.stages.assembly.members[0].partTemplateVersion, 'version.1');
  assert.deepEqual(result.stages.assembly.members[0].partTemplateRef, template);
  assert.equal('selectedId' in result, false);
  assert.equal(JSON.stringify(result).includes('runtime-only'), false);
  assert.ok(result.stages.validation.some(check => check.status === 'pending' && check.passed === false));
});

test('导出保留旧连接和接口字段，并附带可无歧义读取的结构化引用', () => {
  const draft = createComponentDocument();
  draft.assembly.members = [member('a'), member('b')];
  draft.assembly.mates = [{ id: 'm', from: source('a'), to: source('b'), relation: '贴合', offset: 0 }];
  draft.ports = [{ id: 'p', name: '接口', source: source('a'), tags: [] }];
  const result = JSON.parse(serializeComponent(draft));
  assert.equal(result.stages.assembly.mates[0].from, 'a.face.left');
  assert.deepEqual(result.stages.assembly.mates[0].fromRef, { memberId: 'a', interfaceId: 'face.left' });
  assert.equal(result.stages.ports[0].sourceMemberId, 'a');
  assert.equal(result.stages.ports[0].sourceInterfaceId, 'face.left');
  assert.equal(result.stages.assembly.members[0].bindings.length, 2400);
});

test('定义阶段的项目元数据、两级连接说明、约束和证据引用会保存并导出', () => {
  const original = createComponentDocument();
  const evidenceFiles = [{ id: 'evidence-1', fileName: '连接示意.png', mediaType: 'image/png', size: 12, sha256: 'b'.repeat(64), dataUrl: 'data:image/png;base64,AA==' }];
  const draft = componentReducer(original, { type: 'definition.patch', patch: {
    projectCode: 'RACK-2026-001', owner: '张工', organization: '结构组', searchTags: ['货架', '横梁'],
    partConnectionType: '自定义', partConnectionCustom: '零部件采用端板螺栓连接',
    componentConnectionType: '卡扣连接', designConstraints: '横梁长度可调，端部需避开立柱加强筋', evidenceFiles,
  } });
  const exported = JSON.parse(serializeComponent(draft));
  assert.equal(draft.definition.projectCode, 'RACK-2026-001');
  assert.equal(draft.definition.code, '');
  assert.equal(draft.definition.owner, '张工');
  assert.equal(exported.stages.definition.organization, '结构组');
  assert.deepEqual(exported.stages.definition.searchTags, ['货架', '横梁']);
  assert.equal(exported.stages.definition.partConnectionCustom, '零部件采用端板螺栓连接');
  assert.equal(exported.stages.definition.componentConnectionType, '卡扣连接');
  assert.equal(exported.stages.definition.designConstraints, '横梁长度可调，端部需避开立柱加强筋');
  assert.deepEqual(exported.stages.definition.evidenceFiles, evidenceFiles);
});
