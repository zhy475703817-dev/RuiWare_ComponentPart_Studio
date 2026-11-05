import assert from 'node:assert/strict';
import test from 'node:test';
import { memberBounds } from '../src/library/memberModels.ts';
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
