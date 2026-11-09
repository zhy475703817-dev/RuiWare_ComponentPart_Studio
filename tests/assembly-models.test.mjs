import assert from 'node:assert/strict';
import test from 'node:test';
import { memberBounds, memberGroundPosition } from '../src/library/memberModels.ts';
import { createComponentDocument } from '../src/domain/component.ts';

const member = (id, patch = {}) => ({ id, name: id, code: 'PART', role: id,
  template: { publicationId: id, templateId: 'draft', version: 1, sha256: 'a'.repeat(64) },
  position: [0, 0, 0], rotation: [0, 0, 0], parameterValues: {}, interfaces: [], ...patch });
const stl = 'solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 -10\nvertex 100 0 -10\nvertex 0 40 30\nendloop\nendfacet\nendsolid test';

test('加入排位复用成员模型，世界包围盒包含已保存的旋转和平移', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => { requests++; return new Response(stl); });
  const source = member('placement-part', { position: [200, 0, 10], rotation: [0, 0, Math.PI / 2] });
  const bounds = await memberBounds(source);
  assert.ok(Math.abs(bounds.min.x - 160) < 1e-6);
  assert.ok(Math.abs(bounds.max.y - 100) < 1e-6);
  assert.equal(bounds.min.z, 0);
  const again = await memberBounds(source);
  assert.equal(again.equals(bounds), true);
  assert.equal(requests, 1);
  assert.deepEqual(source.position, [200, 0, 10]);
});

test('排位读取失败会拒绝加入准备，不生成伪造模型或坐标', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ error: { code: 'MODEL_MISSING' } }, { status: 422 }));
  await assert.rejects(memberBounds(member('placement-unavailable')), /检查模板内容/);
});

test('现有子组件的内部成员变换会计入新零件的避让范围', async t => {
  const child = member('nested-placement-part', { position: [100, 0, 10] });
  const parent = member('nested-placement-component', { template: {
    templateKind: 'component', publicationId: 'nested-placement-component', templateId: 'draft', version: 1, sha256: 'a'.repeat(64),
  }, position: [1000, 0, 0] });
  const document = createComponentDocument();
  document.assembly.members = [child];
  t.mock.method(globalThis, 'fetch', async url => String(url).includes('published-components')
    ? Response.json({ ...parent.template, document }) : new Response(stl));
  const bounds = await memberBounds(parent);
  assert.deepEqual(bounds.min.toArray(), [1100, 0, 0]);
  assert.deepEqual(bounds.max.toArray(), [1200, 40, 40]);
});

const triangleStl = (height = 80) => `solid triangle
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 100 0 40
vertex 0 40 ${height}
endloop
endfacet
endsolid triangle`;
const component = (id, patch = {}) => member(id, { template: {
  templateKind: 'component', publicationId: id, templateId: 'draft', version: 1, sha256: 'a'.repeat(64),
}, ...patch });

test('旋转后的非对称零件按真实顶点贴地，不被空包围盒角点抬高', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => { requests++; return new Response(triangleStl()); });
  const source = member('ground-triangle', { position: [200, -50, 30], rotation: [0, Math.PI / 4, 0] });
  const next = await memberGroundPosition(source);
  assert.deepEqual(next.slice(0, 2), [200, -50]);
  assert.ok(Math.abs(next[2] - 60 / Math.sqrt(2)) < 1e-6);
  const bounds = await memberBounds(source);
  assert.ok(30 - bounds.min.z > next[2] + 20, 'AABB 空角会错误地将物体额外抬高');
  assert.deepEqual(await memberGroundPosition({ ...source, position: next }), next);
  assert.deepEqual(source.position, [200, -50, 30]);
  assert.equal(requests, 1, '重复贴地与包围盒读取复用同一模型缓存');
});

test('嵌套组件贴地组合全部祖先的旋转和平移', async t => {
  const part = member('ground-nested-part', { position: [10, 20, 30], rotation: [0, 0, Math.PI / 2] });
  const inner = component('ground-inner', { position: [0, 5, 7], rotation: [Math.PI / 2, 0, 0] });
  const outer = component('ground-outer', { position: [200, 300, 400], rotation: [0, Math.PI / 2, 0] });
  const innerDocument = createComponentDocument();
  innerDocument.assembly.members = [part];
  const outerDocument = createComponentDocument();
  outerDocument.assembly.members = [inner];
  t.mock.method(globalThis, 'fetch', async url => {
    if (String(url).endsWith('/ground-outer')) return Response.json({ ...outer.template, document: outerDocument });
    if (String(url).endsWith('/ground-inner')) return Response.json({ ...inner.template, document: innerDocument });
    return new Response(triangleStl());
  });
  // The three vertices end at Z = 390, 390, 430 after all transforms.
  const next = await memberGroundPosition(outer);
  assert.deepEqual(next.slice(0, 2), [200, 300]);
  assert.ok(Math.abs(next[2] - 10) < 1e-6);
  assert.deepEqual(outer.position, [200, 300, 400]);
});

test('组件贴地包含参数重建与阵列实例，不遗漏旋转到下方的实例', async t => {
  const part = member('ground-pattern-part', { position: [0, 0, 10], parameterValues: { height: 80 },
    parameterDefinitions: [{ id: 'height', label: '高度', unit: 'mm', valueType: 'number', default: 80, minimum: 1, maximum: 500 }] });
  const parent = component('ground-pattern-component', {
    position: [10, 20, 500], rotation: [Math.PI, 0, 0], parameterValues: { height: 120, count: 3, spacing: 50 },
  });
  const document = createComponentDocument();
  document.assembly.members = [part];
  document.parameters = [
    { id: 'height', label: '高度', unit: 'mm', value: 80, min: 1, max: 500 },
    { id: 'count', label: '数量', unit: '', value: 1, min: 1, max: 10 },
    { id: 'spacing', label: '间距', unit: 'mm', value: 10, min: 0, max: 500 },
  ];
  document.bindings = [{ id: 'height-binding', parameterId: 'height', memberId: part.id, memberParameterId: 'height' }];
  document.patterns = [{ id: 'stack', label: '堆叠', memberIds: [part.id], countParameterId: 'count', spacingParameterId: 'spacing', axis: 'z' }];
  const values = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (String(url).includes('published-components')) return Response.json({ ...parent.template, document });
    const parameters = JSON.parse(options.body).parameters;
    values.push(parameters);
    return new Response(triangleStl(parameters.height));
  });
  const next = await memberGroundPosition(parent);
  assert.deepEqual(next.slice(0, 2), [10, 20]);
  assert.ok(Math.abs(next[2] - 230) < 1e-6, '120mm 高度 + 10mm 偏置 + 两次 50mm 间距');
  assert.deepEqual(values, [{ height: 120 }]);
});

test('贴地拒绝缺失或无效模型，并允许失败后重试', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    requests++;
    if (requests === 1) return Response.json({ error: { code: 'MODEL_MISSING' } }, { status: 422 });
    if (requests === 2) return new Response('solid empty\nendsolid empty');
    return new Response(triangleStl());
  });
  const source = member('ground-retry');
  await assert.rejects(memberGroundPosition(source), /检查模板内容/);
  await assert.rejects(memberGroundPosition(source), /没有有效的三角形/);
  assert.deepEqual(await memberGroundPosition(source), [0, 0, 0]);
  assert.equal(requests, 3);
});

test('贴地拒绝空组件、循环组件与过期发布版本', async t => {
  const empty = component('ground-empty');
  const cyclic = component('ground-cycle');
  const changed = component('ground-changed');
  const emptyDocument = createComponentDocument();
  const cyclicDocument = createComponentDocument();
  cyclicDocument.assembly.members = [cyclic];
  t.mock.method(globalThis, 'fetch', async url => {
    if (String(url).endsWith('/ground-cycle')) return Response.json({ ...cyclic.template, document: cyclicDocument });
    if (String(url).endsWith('/ground-changed')) return Response.json({ ...changed.template, version: 2, document: emptyDocument });
    return Response.json({ ...empty.template, document: emptyDocument });
  });
  await assert.rejects(memberGroundPosition(empty), /没有有效几何/);
  await assert.rejects(memberGroundPosition(cyclic), /组件嵌套关系/);
  await assert.rejects(memberGroundPosition(changed), /发布版本已变化/);
});

test('贴地拒绝非有限位置和旋转而不请求模型', async t => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected fetch'); });
  await assert.rejects(memberGroundPosition(member('ground-invalid-position', { position: [0, Infinity, 0] })), /位置或旋转数值无效/);
  await assert.rejects(memberGroundPosition(member('ground-invalid-rotation', { rotation: [0, NaN, 0] })), /位置或旋转数值无效/);
  assert.equal(fetchMock.mock.callCount(), 0);
});
