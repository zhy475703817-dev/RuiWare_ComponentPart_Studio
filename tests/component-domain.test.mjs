import assert from 'node:assert/strict';
import test from 'node:test';
import { createComponentDocument } from '../src/domain/component.ts';
import { componentReducer } from '../src/domain/componentReducer.ts';
import { validateComponent } from '../src/domain/validation.ts';
import { serializeComponent } from '../src/domain/serialization.ts';
import { deriveComponentMetrics, effectiveParameterBounds } from '../src/domain/parameterMetrics.ts';
import { resolveComponentComposition } from '../src/domain/composition.ts';

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

test('组件间构成规则默认为空，并与其他限制条件独立保存和删除', () => {
  let draft = createComponentDocument();
  assert.equal(draft.rules.some(rule => rule.kind === 'composition'), false);
  draft = componentReducer(draft, { type: 'rule.add', rule: { id: 'clearance', kind: 'constraint', label: '安装间隙', expression: '间隙 >= 20 mm' } });
  draft = componentReducer(draft, { type: 'rule.add', rule: { id: 'width-spacing', kind: 'composition', label: '宽度变化时保持间距', expression: '组件总宽变化时，组件间距保持 20 mm' } });
  const exported = JSON.parse(serializeComponent(draft));
  assert.equal(exported.stages.rules.find(rule => rule.id === 'width-spacing').kind, 'composition');
  assert.equal(exported.stages.rules.find(rule => rule.id === 'clearance').kind, 'constraint');
  draft = componentReducer(draft, { type: 'rule.remove', id: 'clearance' });
  assert.equal(draft.rules.some(rule => rule.id === 'clearance'), false);
  assert.equal(draft.rules.some(rule => rule.id === 'width-spacing'), true);
});

test('新增成员时按长宽高名称默认绑定，并允许移除尺寸来源', () => {
  const boundMember = { ...member('sized'), parameterDefinitions: [
    { id: 'length', label: '零件长度', unit: 'mm', valueType: 'number', minimum: 1000, maximum: 6000, default: 2400 },
    { id: 'width', label: '零件宽度', unit: 'mm', valueType: 'number', minimum: 100, maximum: 3000, default: 600 },
    { id: 'height', label: '零件高度', unit: 'mm', valueType: 'number', minimum: 300, maximum: 8000, default: 1800 },
  ] };
  let draft = componentReducer(createComponentDocument(), { type: 'member.add', member: boundMember });
  assert.deepEqual(draft.bindings.map(binding => binding.parameterId), ['componentLength', 'componentWidth', 'componentHeight']);
  assert.deepEqual(draft.assembly.members[0].parameterValues, { length: 2400, width: 600, height: 1800 });
  const lengthBinding = draft.bindings.find(binding => binding.parameterId === 'componentLength');
  draft = componentReducer(draft, { type: 'binding.set', id: lengthBinding.id, binding: null });
  assert.equal(draft.bindings.some(binding => binding.parameterId === 'componentLength'), false);
});

test('高度指标保留组件参数值，不从其他参数推算几何高度', () => {
  const metrics = deriveComponentMetrics([
    { id: 'componentHeight', label: '组件总高', value: 1800, unit: 'mm', min: 500, max: 6000 },
  ]);
  assert.equal(metrics.height.value, '1,800 mm');
  assert.match(metrics.height.note, /组件参数范围/);
});

test('体积由长宽高计算，重量只在存在材料信息时计算', () => {
  const missing = deriveComponentMetrics([]);
  assert.equal(missing.volume.value, '未配置');
  assert.match(missing.volume.note, /已放置成员/);
  assert.equal(missing.weight.value, '未配置');
  assert.match(missing.weight.note, /材料密度/);

  const dimensions = [
    { id: 'componentLength', label: '组件总长', value: 100, unit: 'mm', min: 10, max: 500 },
    { id: 'componentWidth', label: '组件总宽', value: 50, unit: 'mm', min: 10, max: 200 },
    { id: 'componentHeight', label: '组件总高', value: 20, unit: 'mm', min: 10, max: 200 },
  ];
  const withoutMaterial = deriveComponentMetrics(dimensions, [], [member('a')]);
  assert.equal(withoutMaterial.volume.label, '包络体积');
  assert.equal(withoutMaterial.volume.value, '0.000 m³');
  assert.match(withoutMaterial.volume.note, /不代表材料实体体积/);
  assert.equal(withoutMaterial.weight.value, '未配置');

  const withDensity = deriveComponentMetrics([...dimensions, { id: 'materialDensity', label: '材料密度', value: 7850, unit: 'kg/m3', min: 7000, max: 9000 }], [], [member('a')]);
  assert.equal(withDensity.weight.value, '未配置');
  assert.match(withDensity.weight.note, /包络体积不用于估算重量/);

  const solidPart = { ...member('solid'), parameterValues: { solidVolume: 100000, density: 7850 }, parameterDefinitions: [
    { id: 'solidVolume', label: '零件实体体积', unit: 'mm3', valueType: 'number', minimum: 0, maximum: 500000, default: 100000 },
    { id: 'density', label: '材料密度', unit: 'kg/m3', valueType: 'number', minimum: 7000, maximum: 9000, default: 7850 },
  ] };
  const withSolidProperties = deriveComponentMetrics(dimensions, [], [solidPart]);
  assert.equal(withSolidProperties.weight.value, '0.785 kg');

  const weighedMember = { ...member('b'), parameterValues: { mass: 500 }, parameterDefinitions: [{ id: 'mass', label: '质量', unit: 'g', valueType: 'number', minimum: 0, maximum: 1000, default: 500 }] };
  const withMass = deriveComponentMetrics(dimensions, [], [weighedMember]);
  assert.equal(withMass.weight.value, '0.500 kg');
});

test('组件参数绑定会继承成员范围并把值同步到成员参数', () => {
  const base = createComponentDocument();
  const boundMember = { ...member('a'), parameterDefinitions: [{ id: 'length', label: '零件长度', unit: 'mm', valueType: 'number', minimum: 1800, maximum: 4200, default: 2400 }] };
  let draft = componentReducer(base, { type: 'member.add', member: boundMember });
  const binding = { id: 'b1', parameterId: 'componentLength', memberId: 'a', memberParameterId: 'length' };
  draft = componentReducer(draft, { type: 'binding.set', id: binding.id, binding });
  const bounds = effectiveParameterBounds(draft.parameters[0], draft.bindings, draft.assembly.members);
  assert.equal(bounds.min, 1800);
  assert.equal(bounds.max, 4200);
  draft = componentReducer(draft, { type: 'parameter.value', id: 'componentLength', value: 5000 });
  assert.equal(draft.parameters[0].value, 4200);
  assert.equal(draft.assembly.members[0].parameterValues.length, 4200);
  assert.equal(JSON.parse(serializeComponent(draft)).stages.bindings[0].memberParameterId, 'length');
});

test('清空组件参数时移除绑定覆盖并让零部件回到发布默认值', () => {
  const boundMember = { ...member('a'), parameterDefinitions: [{ id: 'length', label: '零件长度', unit: 'mm', valueType: 'number', minimum: 1800, maximum: 4200, default: 2400 }] };
  let draft = componentReducer(createComponentDocument(), { type: 'member.add', member: boundMember });
  draft = componentReducer(draft, { type: 'binding.set', id: 'b1', binding: { id: 'b1', parameterId: 'componentLength', memberId: 'a', memberParameterId: 'length' } });
  draft = componentReducer(draft, { type: 'parameter.value', id: 'componentLength', value: 3600 });
  assert.equal(draft.assembly.members[0].parameterValues.length, 3600);
  draft = componentReducer(draft, { type: 'parameter.value', id: 'componentLength', value: null });
  assert.equal(draft.parameters[0].value, null);
  assert.equal(Object.hasOwn(draft.assembly.members[0].parameterValues, 'length'), false);
  assert.equal(validateComponent(draft).find(check => check.id === 'parameters').status, 'fail');
});

test('整数型成员绑定会把有效范围收敛到整数并同步整数值', () => {
  const boundMember = { ...member('integer-member'), parameterDefinitions: [{ id: 'count', label: '数量', unit: '件', valueType: 'integer', minimum: 2.2, maximum: 8.7, default: 4 }] };
  let draft = componentReducer(createComponentDocument(), { type: 'parameter.add', parameter: { id: 'componentCount', label: '组件数量', value: 4, unit: '件', min: 1, max: 20 } });
  draft = componentReducer(draft, { type: 'member.add', member: boundMember });
  draft = componentReducer(draft, { type: 'binding.set', id: 'count-binding', binding: { id: 'count-binding', parameterId: 'componentCount', memberId: 'integer-member', memberParameterId: 'count' } });
  assert.equal(draft.parameters.at(-1).value, 4);
  draft = componentReducer(draft, { type: 'parameter.value', id: 'componentCount', value: 9.4 });
  assert.equal(draft.parameters.at(-1).value, 8);
  assert.equal(draft.assembly.members[0].parameterValues.count, 8);
  assert.equal(effectiveParameterBounds(draft.parameters.at(-1), draft.bindings, draft.assembly.members).min, 3);
});

test('组件参数可以新增和删除，并清理关联绑定', () => {
  const parameter = { id: 'componentWidth', label: '组件宽度', value: 800, unit: 'mm', min: 100, max: 2000 };
  let draft = componentReducer(createComponentDocument(), { type: 'parameter.add', parameter });
  const boundMember = { ...member('a'), parameterValues: { width: 900 }, parameterDefinitions: [{ id: 'width', label: '宽度', unit: 'mm', valueType: 'number', minimum: 200, maximum: 1500, default: 900 }] };
  draft = componentReducer(draft, { type: 'member.add', member: boundMember });
  assert.equal(draft.parameters.some(item => item.id === 'componentWidth'), true);
  draft = componentReducer(draft, { type: 'binding.set', id: 'b-width', binding: { id: 'b-width', parameterId: 'componentWidth', memberId: 'a', memberParameterId: 'width' } });
  assert.equal(draft.bindings.filter(item => item.parameterId === 'componentWidth').length, 1);
  draft = componentReducer(draft, { type: 'parameter.remove', id: 'componentWidth' });
  assert.equal(draft.parameters.some(item => item.id === 'componentWidth'), false);
  assert.equal(draft.bindings.some(item => item.parameterId === 'componentWidth'), false);
});

test('通用构成规则按数量、间距和轴保存，并在删除来源时清理成员', () => {
  let draft = createComponentDocument();
  draft = componentReducer(draft, { type: 'parameter.add', parameter: { id: 'supportCount', label: '支撑数量', value: 3, unit: '个', min: 1, max: 8 } });
  draft = componentReducer(draft, { type: 'parameter.add', parameter: { id: 'supportPitch', label: '支撑间距', value: 450, unit: 'mm', min: 0, max: 1200 } });
  draft = componentReducer(draft, { type: 'member.add', member: member('support') });
  draft = componentReducer(draft, { type: 'member.add', member: member('brace') });
  const pattern = { id: 'support-array', label: '支撑阵列', memberIds: ['support', 'brace'], countParameterId: 'supportCount', spacingParameterId: 'supportPitch', axis: 'z' };
  draft = componentReducer(draft, { type: 'pattern.add', pattern });
  assert.deepEqual(draft.patterns, [pattern]);
  const result = JSON.parse(serializeComponent(draft));
  assert.equal(result.stages.patterns[0].countParameterId, 'supportCount');
  assert.equal(validateComponent(draft).find(check => check.id === 'patterns').status, 'pass');
  draft = componentReducer(draft, { type: 'member.remove', id: 'support' });
  assert.deepEqual(draft.patterns[0].memberIds, ['brace']);
  draft = componentReducer(draft, { type: 'parameter.remove', id: 'supportCount' });
  assert.equal(draft.patterns.length, 0);
});

test('组件构成按参数绑定驱动零部件并重复完整成员组', () => {
  let draft = createComponentDocument();
  draft = componentReducer(draft, { type: 'parameter.add', parameter: { id: 'supportCount', label: '支撑数量', value: 3, unit: '个', min: 1, max: 8 } });
  draft = componentReducer(draft, { type: 'parameter.add', parameter: { id: 'supportPitch', label: '支撑间距', value: 450, unit: 'mm', min: 0, max: 1200 } });
  draft = componentReducer(draft, { type: 'parameter.add', parameter: { id: 'beamWidth', label: '梁宽', value: 600, unit: 'mm', min: 200, max: 1000 } });
  const beam = { ...member('beam'), parameterDefinitions: [{ id: 'width', label: '零件宽度', unit: 'mm', valueType: 'number', minimum: 250, maximum: 900, default: 500 }] };
  const brace = member('brace');
  brace.position = [25, 0, 0];
  draft = componentReducer(draft, { type: 'member.add', member: beam });
  draft = componentReducer(draft, { type: 'member.add', member: brace });
  draft = componentReducer(draft, { type: 'binding.set', id: 'width-binding', binding: { id: 'width-binding', parameterId: 'beamWidth', memberId: 'beam', memberParameterId: 'width' } });
  draft = componentReducer(draft, { type: 'pattern.add', pattern: { id: 'support-group', label: '支撑组', memberIds: ['beam', 'brace'], countParameterId: 'supportCount', spacingParameterId: 'supportPitch', axis: 'z' } });

  const instances = resolveComponentComposition(draft, { supportCount: 4.4, supportPitch: 300, beamWidth: 1200 });
  assert.equal(instances.length, 8);
  assert.deepEqual(instances.slice(0, 4).map(item => item.position[2]), [0, 300, 600, 900]);
  assert.deepEqual(instances.slice(4).map(item => item.position), [[25, 0, 0], [25, 0, 300], [25, 0, 600], [25, 0, 900]]);
  assert.ok(instances.slice(0, 4).every(item => item.parameterValues.width === 900));
  assert.equal(instances[1].sourceMemberId, 'beam');
  assert.equal(instances[1].patternInstance, true);
});

test('嵌套组件保留其内部绑定和构成规则', () => {
  let nested = createComponentDocument();
  nested.parameters = [
    { id: 'memberCount', label: '成员数量', value: 2, unit: '个', min: 1, max: 6 },
    { id: 'memberPitch', label: '成员间距', value: 200, unit: 'mm', min: 50, max: 1000 },
    { id: 'railLength', label: '梁长', value: 1000, unit: 'mm', min: 500, max: 2500 },
  ];
  const rail = { ...member('rail'), parameterDefinitions: [{ id: 'length', label: '零件长度', unit: 'mm', valueType: 'number', minimum: 500, maximum: 2000, default: 1000 }] };
  nested.assembly.members = [rail];
  nested.bindings = [{ id: 'rail-length-binding', parameterId: 'railLength', memberId: 'rail', memberParameterId: 'length' }];
  nested.patterns = [{ id: 'rail-array', label: '梁组', memberIds: ['rail'], countParameterId: 'memberCount', spacingParameterId: 'memberPitch', axis: 'x' }];

  const wrapper = createComponentDocument();
  wrapper.parameters = [
    { id: 'outerCount', label: '外层数量', value: 3, unit: '个', min: 1, max: 8 },
    { id: 'outerPitch', label: '外层间距', value: 300, unit: 'mm', min: 0, max: 1200 },
    { id: 'outerRailLength', label: '外层梁长', value: 1400, unit: 'mm', min: 500, max: 2000 },
  ];
  const nestedMember = {
    ...member('nested-component'),
    template: { ...template, templateKind: 'component' },
    parameterValues: { memberCount: 2, memberPitch: 200, railLength: 1000 },
    parameterDefinitions: nested.parameters.map(parameter => ({ id: parameter.id, label: parameter.label, unit: parameter.unit, valueType: ['个'].includes(parameter.unit) ? 'integer' : 'number', minimum: parameter.min, maximum: parameter.max, default: parameter.value })),
  };
  wrapper.assembly.members = [nestedMember];
  wrapper.bindings = [
    { id: 'count-binding', parameterId: 'outerCount', memberId: 'nested-component', memberParameterId: 'memberCount' },
    { id: 'pitch-binding', parameterId: 'outerPitch', memberId: 'nested-component', memberParameterId: 'memberPitch' },
    { id: 'length-binding', parameterId: 'outerRailLength', memberId: 'nested-component', memberParameterId: 'railLength' },
  ];

  const wrapperMembers = resolveComponentComposition(wrapper);
  const nestedMembers = resolveComponentComposition(nested, wrapperMembers[0].parameterValues);
  assert.equal(nestedMembers.length, 3);
  assert.deepEqual(nestedMembers.map(item => item.position[0]), [0, 300, 600]);
  assert.ok(nestedMembers.every(item => item.parameterValues.length === 1400));
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
  const sourceInterface = {
    ...draft.assembly.members[0].interfaces[0], declarationMode: 'featureDerived', interfaceType: 'adjustable',
    geometryRefs: ['slotFace'], parameterRefs: ['slotWidth'], region: { mode: 'rectangle', uSpanExpression: 'slotWidth', vSpan: 12, countExpression: 'slotCount' },
  };
  draft.assembly.members[0].interfaces[0] = sourceInterface;
  const mate = { id: 'mate', from: source('member.a'), to: source('member.b'), relation: '贴合', offset: 0 };
  draft = componentReducer(draft, { type: 'mate.add', mate });
  assert.equal(draft.assembly.mates[0].from.interfaceId, 'face.left');
  assert.equal(componentReducer(draft, { type: 'mate.add', mate: { ...mate, id: 'invalid', to: source('missing') } }), draft);
  const port = { id: 'port', name: '外部接口', source: source('member.a'), tags: [] };
  draft = componentReducer(draft, { type: 'port.add', port });
  assert.equal(draft.ports.length, 1);
  assert.deepEqual(draft.ports[0].interface, sourceInterface);
  assert.equal(JSON.parse(serializeComponent(draft)).stages.ports[0].interface.region.countExpression, 'slotCount');
  assert.equal(componentReducer(draft, { type: 'port.add', port: { id: 'stale', name: '过期接口', source: source('member.b'), tags: [], interface: { ...sourceInterface, name: 'stale' } } }), draft);
  const staleSnapshot = { ...draft, ports: [{ ...draft.ports[0], interface: { ...sourceInterface, name: '旧名称' } }] };
  assert.equal(validateComponent(staleSnapshot).find(check => check.id === 'ports').status, 'fail');
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

test('项目定义扩展字段可通过补丁更新并完整导出', () => {
  let draft = createComponentDocument();
  const patch = {
    projectCode: 'RACK-2026-01', owner: '张工', organization: '研发部', searchTags: ['横梁', '可调'],
    connectionForms: {
      internal: { mode: 'preset', value: '螺栓连接', details: 'M8 紧固' },
      interComponent: { mode: 'custom', value: '挂钩插接', details: '免工具拆装' },
    },
    designConstraints: '跨度不超过 2400 mm，层高可调范围 50 mm',
    evidence: [{ id: 'e1', name: '安装参考.png', mediaType: 'image/png', size: 1024, addedAt: '2026-10-28T00:00:00.000Z' }],
  };
  draft = componentReducer(draft, { type: 'definition.patch', patch });
  assert.equal(draft.definition.projectCode, patch.projectCode);
  assert.equal(draft.definition.connectionForms.interComponent.mode, 'custom');
  assert.deepEqual(draft.definition.searchTags, patch.searchTags);
  const result = JSON.parse(serializeComponent(draft));
  assert.deepEqual(result.stages.definition, { ...createComponentDocument().definition, ...patch });
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
