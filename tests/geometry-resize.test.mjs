import assert from 'node:assert/strict';
import test from 'node:test';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { resolveComponentComposition } from '../src/domain/composition.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { componentPublicationMember } from '../src/library/componentPublications.ts';

function fixture() {
  const document = createComponentDocument();
  const member = { id: 'part', name: '零件', code: 'PART', role: '杆件', template: { publicationId: 'fixed', templateId: 'fixed', version: 1, sha256: 'a'.repeat(64) }, parameterValues: { length: 150 }, position: [0, 0, 0], rotation: [0, 0, 0], interfaces: [{ id: 'start', name: '端面', type: 'plane', tags: [] }], parameterDefinitions: [{ id: 'length', label: '长度', unit: 'mm', valueType: 'number', default: 100, minimum: 10, maximum: 200 }] };
  document.assembly.members = [member];
  document.geometryDesign = { version: 1, axes: 'xyz', anchor: 'bottom-center', locks: [], relations: [], dimensions: [10, 10, 150], solverVersion: 'geometry-resize-1', inputHash: 'hash', verified: true };
  document.schemaVersion = 'component-1.1';
  document.bindings = [{ id: 'old', parameterId: 'componentLength', memberId: 'part', memberParameterId: 'length' }];
  document.ports = [{ id: 'out', name: '对外端面', source: { memberId: 'part', interfaceId: 'start' }, tags: [], interface: member.interfaces[0] }];
  return document;
}

test('几何模式读取保存的实例，不重新套用旧参数或阵列', () => {
  const document = fixture();
  document.parameters.push({ id: 'count', value: 2, label: '数量', unit: '个', min: 1, max: 5 });
  document.patterns = [{ id: 'p', label: '阵列', memberIds: ['part'], countParameterId: 'count', spacingParameterId: 'componentLength', axis: 'z' }];
  document.assembly.members.push({ ...structuredClone(document.assembly.members[0]), id: 'part#p-1', position: [0, 0, 150] });
  const members = resolveComponentComposition(document, { componentLength: 5000 });
  assert.equal(members.length, 2);
  assert.equal(members[0].parameterValues.length, 150);
  assert.deepEqual(members[1].position, [0, 0, 150]);
});

test('前端导出保留嵌套结果、几何版本和真实参数值', () => {
  const child = fixture(), parent = fixture();
  parent.assembly.members[0].template.templateKind = 'component';
  parent.assembly.members[0].instanceDocument = child;
  const output = JSON.parse(serializeComponent(parent));
  assert.deepEqual(output.geometryDesign, parent.geometryDesign);
  assert.deepEqual(output.stages.assembly.members[0].instanceDocument, child);
  assert.equal(output.stages.assembly.members[0].partTemplateRef.templateKind, 'component');
  assert.equal(output.stages.assembly.members[0].bindings.length, 150);
});

test('几何模式禁止旧参数旁路，成员编辑使旧验证失效', () => {
  const document = fixture();
  assert.equal(componentReducer(document, { type: 'parameter.value', id: 'componentLength', value: 3000 }), document);
  const changed = componentReducer(document, { type: 'member.patch', id: 'part', patch: { position: [10, 0, 0] } });
  assert.equal(changed.geometryDesign.verified, false);
  assert.equal(document.geometryDesign.verified, true);
});

test('发布组件的两份实例覆盖互不污染发布源', () => {
  const document = fixture();
  const publication = { publicationId: 'component-pub', templateId: 'c', version: 1, sha256: 'b'.repeat(64), name: '组件', code: 'COMP', templateKind: 'component', document };
  const left = componentPublicationMember(publication, 'left'), right = componentPublicationMember(publication, 'right');
  left.instanceDocument = structuredClone(document);
  left.instanceDocument.assembly.members[0].parameterValues.length = 175;
  assert.equal(resolveComponentComposition(left.instanceDocument)[0].parameterValues.length, 175);
  assert.equal(resolveComponentComposition(right.instanceDocument || document)[0].parameterValues.length, 150);
  assert.equal(document.assembly.members[0].parameterValues.length, 150);
});
