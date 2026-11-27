import test from 'node:test';
import assert from 'node:assert/strict';
import { Matrix4, Quaternion, Vector3, Euler } from 'three';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { captureModel, cloneSelectionModel, createSelectionGroup, groupForMember, groupMemberIds, materializeComponentModel, ungroupSelection } from '../src/domain/assemblyGroups.ts';

const member = id => ({ id, name: id, code: id, role: id, template: { publicationId: `pub-${id}`, templateId: id, version: 1, sha256: 'a'.repeat(64) }, position: [0, 0, 0], rotation: [0, 0, 0], parameterValues: {}, interfaces: [{ id: 'face', name: 'face', type: 'plane', tags: [] }] });
const source = memberId => ({ memberId, interfaceId: 'face' });
const document = () => { const result = createComponentDocument(); result.assembly.members = ['a', 'b', 'c'].map(member); return result; };
const factory = () => { let index = 0; return () => `new-${++index}`; };

test('分组保持世界姿态，嵌套后优先选择外层，解组只解除所选层级', () => {
  const draft = document();
  const original = structuredClone(draft.assembly.members);
  let assembly = createSelectionGroup(draft.assembly, ['a', 'b'], '内组件', 'inner');
  assembly = createSelectionGroup(assembly, ['a', 'b', 'c'], '外组件', 'outer');
  assert.equal(groupForMember(assembly, 'a').id, 'outer');
  assert.equal(groupForMember(assembly, 'a', false).id, 'inner');
  assert.deepEqual(groupMemberIds(assembly, 'outer'), ['a', 'b', 'c']);
  assert.deepEqual(assembly.members, original);
  assembly = ungroupSelection(assembly, ['outer']);
  assert.equal(groupForMember(assembly, 'a').id, 'inner');
  assert.equal(groupForMember(assembly, 'c'), undefined);
  assert.equal(assembly.groups[0].parentId, undefined);
  assert.deepEqual(assembly.members, original);
});

test('删除成员清理所有嵌套分组，导出与模型撤销保留分组', () => {
  let draft = document();
  draft.assembly = createSelectionGroup(draft.assembly, ['a'], '内部', 'inner');
  draft.assembly = createSelectionGroup(draft.assembly, ['a', 'b'], '外部', 'outer');
  const model = captureModel(draft);
  draft = componentReducer(draft, { type: 'member.remove', id: 'a' });
  assert.deepEqual(draft.assembly.groups, [{ id: 'outer', name: '外部', memberIds: ['b'] }]);
  const restored = componentReducer(draft, { type: 'model.restore', model });
  assert.equal(restored.assembly.groups.length, 2);
  assert.deepEqual(JSON.parse(serializeComponent(restored)).stages.assembly.groups, model.assembly.groups);
  assert.equal(JSON.parse(serializeComponent(createComponentDocument())).stages.assembly.groups, undefined);
});

test('跨组件选择子件重新分组后，各层级不会重叠引用同一子件', () => {
  const draft = document();
  draft.assembly.members.push(member('d'));
  let assembly = createSelectionGroup(draft.assembly, ['a', 'b'], '左组', 'left');
  assembly = createSelectionGroup(assembly, ['c', 'd'], '右组', 'right');
  assembly = createSelectionGroup(assembly, ['a', 'c'], '新组', 'new');
  assert.deepEqual(groupMemberIds(assembly, 'left'), ['b']);
  assert.deepEqual(groupMemberIds(assembly, 'right'), ['d']);
  assert.deepEqual(groupMemberIds(assembly, 'new'), ['a', 'c']);
  assert.equal(groupForMember(assembly, 'a').id, 'new');
});

test('重新包装拥有相同成员的嵌套组不会形成层级循环', () => {
  const draft = document();
  draft.assembly.groups = [
    { id: 'inner', name: '内层', memberIds: ['a', 'b'], parentId: 'outer' },
    { id: 'outer', name: '外层', memberIds: ['a', 'b'], parentId: 'parent' },
    { id: 'parent', name: '祖先', memberIds: ['a', 'b', 'c'] },
  ];
  const assembly = createSelectionGroup(draft.assembly, ['a', 'b'], '新组', 'new');
  assert.equal(assembly.groups.find(group => group.id === 'inner').parentId, 'outer');
  assert.equal(assembly.groups.find(group => group.id === 'outer').parentId, 'new');
  assert.equal(assembly.groups.find(group => group.id === 'new').parentId, 'parent');
  assert.equal(groupForMember(assembly, 'a').id, 'parent');
  for (const group of assembly.groups) {
    const visited = new Set();
    let current = group;
    while (current) {
      assert.equal(visited.has(current.id), false);
      visited.add(current.id);
      current = assembly.groups.find(item => item.id === current.parentId);
    }
  }
});

test('复制分组重映射内部连接与引用，保留复制后目标草稿的编辑', () => {
  const draft = document();
  draft.assembly.groups = [{ id: 'g', name: '框架', memberIds: ['a', 'b'] }];
  draft.assembly.mates = [{ id: 'inside', from: source('a'), to: source('b'), relation: '贴合', offset: 0 }, { id: 'outside', from: source('b'), to: source('c'), relation: '贴合', offset: 0 }];
  draft.ports = [{ id: 'p', name: '端面', source: source('a'), tags: [] }];
  draft.bindings = [{ id: 'bind', parameterId: 'componentLength', memberId: 'a', memberParameterId: 'length' }];
  draft.slots = [{ id: 'slot', name: '槽', required: false, baseInterface: source('a'), memberId: 'b', interfaceId: 'face' }];
  const current = structuredClone(draft);
  current.assembly.members[2].position = [500, 0, 0];
  const { model, memberIds } = cloneSelectionModel(draft, ['a', 'b'], [100, 200, 300], factory(), current);
  assert.equal(model.assembly.members.length, 5);
  assert.deepEqual(model.assembly.members[2].position, [500, 0, 0]);
  assert.deepEqual(model.assembly.members[3].position, [100, 200, 300]);
  assert.equal(model.assembly.mates.length, 3);
  assert.deepEqual(model.assembly.mates.at(-1).from, source(memberIds[0]));
  assert.deepEqual(model.assembly.mates.at(-1).to, source(memberIds[1]));
  assert.deepEqual(model.assembly.groups.at(-1).memberIds, memberIds);
  assert.equal(model.bindings.at(-1).memberId, memberIds[0]);
  assert.equal(model.ports.at(-1).source.memberId, memberIds[0]);
  assert.equal(model.slots.at(-1).baseInterface.memberId, memberIds[0]);
  assert.equal(model.slots.at(-1).memberId, memberIds[1]);
  assert.deepEqual(draft.assembly.members[0].position, [0, 0, 0]);
  const partial = cloneSelectionModel(draft, ['a'], [0, 0, 0], factory());
  assert.equal(partial.model.assembly.groups.length, 1);
  assert.equal(partial.model.assembly.mates.length, 2);
});

test('多选按偏移复制保留原件和姿态，可用一次模型恢复撤销', () => {
  const draft = document();
  draft.assembly.members[0].position = [10, 20, 30];
  draft.assembly.members[0].rotation = [.1, .2, .3];
  draft.assembly.members[1].position = [-40, 50, 60];
  draft.assembly.members[1].rotation = [-.3, .4, .5];
  const before = captureModel(draft);
  const offset = [25, -75, 100];
  const { model, memberIds } = cloneSelectionModel(draft, ['a', 'b', 'a', 'missing'], offset, factory());
  assert.equal(memberIds.length, 2);
  assert.deepEqual(model.assembly.members.slice(0, 3), before.assembly.members);
  model.assembly.members.slice(3).forEach((copy, index) => {
    const original = before.assembly.members[index];
    assert.deepEqual(copy.position, original.position.map((value, axis) => value + offset[axis]));
    assert.deepEqual(copy.rotation, original.rotation);
    assert.notEqual(copy.id, original.id);
  });
  assert.deepEqual(captureModel(draft), before);
  const updated = componentReducer(draft, { type: 'model.restore', model });
  assert.equal(updated.assembly.members.length, 5);
  assert.deepEqual(captureModel(componentReducer(updated, { type: 'model.restore', model: before })), before);
});

test('已发布组件逐层展开为可编辑零件，保持世界矩阵与外部连接', async () => {
  const draft = document();
  const nested = member('nested'); nested.template.templateKind = 'component'; nested.position = [100, 200, 300]; nested.rotation = [.2, .3, .4];
  draft.assembly.members = [nested, member('outside')];
  draft.assembly.mates = [{ id: 'external', from: { memberId: 'nested', interfaceId: 'external-port' }, to: source('outside'), relation: '贴合', offset: 0 }];
  const publication = createComponentDocument();
  const child = member('child'); child.position = [20, 0, 0]; child.rotation = [.1, .5, 0];
  publication.assembly.members = [child];
  publication.ports = [{ id: 'external-port', name: '外部端口', source: source('child'), tags: [] }];
  const result = await materializeComponentModel(draft, 'nested', async () => publication, { childPath: ['child'], idFactory: factory() });
  assert.deepEqual(result.memberIds, [result.selectedMemberId]);
  assert.equal(result.groupId, 'nested');
  assert.equal(result.model.assembly.members.length, 2);
  const actual = result.model.assembly.members.find(item => item.id === result.selectedMemberId);
  const matrix = item => new Matrix4().compose(new Vector3(...item.position), new Quaternion().setFromEuler(new Euler(...item.rotation)), new Vector3(1, 1, 1));
  const expected = matrix(nested).multiply(matrix(child));
  matrix(actual).elements.forEach((value, index) => assert.ok(Math.abs(value - expected.elements[index]) < 1e-8));
  assert.equal('sourceMemberId' in actual, false);
  assert.equal(result.model.assembly.mates[0].from.memberId, actual.id);
  assert.equal(result.model.assembly.mates[0].from.interfaceId, 'face');
  assert.deepEqual(result.model.assembly.groups[0].memberIds, [actual.id]);
  const ungrouped = await materializeComponentModel(draft, 'nested', async () => publication, { keepGroup: false, idFactory: factory() });
  assert.deepEqual(ungrouped.model.assembly.groups, []);
});

test('递归发布组件保留内部层级且拒绝循环引用', async () => {
  const draft = createComponentDocument();
  const root = member('root'); root.template.templateKind = 'component'; draft.assembly.members = [root];
  const publication = createComponentDocument(), nestedPublication = createComponentDocument();
  const inner = member('inner'); inner.template.templateKind = 'component'; publication.assembly.members = [inner];
  nestedPublication.assembly.members = [member('leaf')];
  const loader = async id => id === 'pub-root' ? publication : nestedPublication;
  const result = await materializeComponentModel(draft, 'root', loader, { childPath: ['inner', 'leaf'], idFactory: factory() });
  assert.equal(result.memberIds[0], result.selectedMemberId);
  assert.equal(result.model.assembly.groups.length, 2);
  assert.equal(groupForMember(result.model.assembly, result.selectedMemberId).id, 'root');
  nestedPublication.assembly.members = [root];
  await assert.rejects(materializeComponentModel(draft, 'root', loader), /循环引用/);
});

test('多选复制保留撑杆规则，删除后的模型撤销恢复规则与成员', () => {
  const draft = document();
  draft.braceGenerationRules = [{
    id: 'brace-rule', label: '撑杆规则', heightParameterId: 'componentHeight', pitchParameterId: 'componentLength',
    bottomOffsetParameterId: 'bottom', topClearanceParameterId: 'top',
    leftColumnMemberId: 'a', rightColumnMemberId: 'b', horizontalMemberId: 'c', diagonalMemberId: 'diagonal',
    horizontalLeftInterfaceId: 'face', horizontalRightInterfaceId: 'face', diagonalLeftInterfaceId: 'face', diagonalRightInterfaceId: 'face',
  }];
  draft.assembly.members.push(member('diagonal'));
  const before = captureModel(draft);
  const copied = cloneSelectionModel(draft, ['a', 'b'], [20, 0, 0], factory());
  assert.deepEqual(copied.model.braceGenerationRules, before.braceGenerationRules);
  assert.notStrictEqual(copied.model.braceGenerationRules, draft.braceGenerationRules);
  let removed = componentReducer(draft, { type: 'member.remove', id: 'a' });
  removed = componentReducer(removed, { type: 'model.restore', model: { ...captureModel(removed), braceGenerationRules: [] } });
  assert.equal(removed.braceGenerationRules.length, 0);
  const restored = componentReducer(removed, { type: 'model.restore', model: before });
  assert.deepEqual(captureModel(restored), before);
});

test('展开尺寸调整后的嵌套组件使用已保存实例，保留真实尺寸与世界位置', async () => {
  const draft = createComponentDocument();
  const root = member('root'); root.template.templateKind = 'component'; root.position = [100, 0, 0];
  const inner = member('inner'); inner.template.templateKind = 'component'; inner.position = [20, 0, 0];
  const leaf = member('leaf'); leaf.position = [7, 0, 0]; leaf.parameterValues = { length: 180 };
  leaf.parameterDefinitions = [{ id: 'length', label: '长度', unit: 'mm', valueType: 'number', minimum: 1, maximum: 6000, default: 100 }];
  const resized = createComponentDocument();
  resized.assembly.members = [leaf];
  resized.bindings = [{ id: 'binding', memberId: 'leaf', memberParameterId: 'length', parameterId: 'componentLength' }];
  resized.geometryDesign = { version: 1, axes: 'xyz', anchor: 'bottom-center', locks: [], relations: [], dimensions: [180, 80, 60], solverVersion: 'test', inputHash: 'test', verified: true };
  inner.instanceDocument = resized;
  root.instanceDocument = createComponentDocument();
  root.instanceDocument.assembly.members = [inner];
  draft.assembly.members = [root];
  const before = structuredClone(draft);
  const result = await materializeComponentModel(draft, 'root', async () => { throw new Error('不得用发布模板覆盖实例尺寸'); }, { childPath: ['inner', 'leaf'], idFactory: factory() });
  const expanded = result.model.assembly.members.find(item => item.id === result.selectedMemberId);
  assert.deepEqual(expanded.position, [127, 0, 0]);
  assert.deepEqual(expanded.parameterValues, { length: 180 });
  assert.equal(result.model.assembly.members.length, 1);
  assert.deepEqual(draft, before);
});
