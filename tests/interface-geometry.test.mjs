import assert from 'node:assert/strict';
import test from 'node:test';
import { hasInterfaceSelection, interfaceHighlights, sameInterfaceSelection, semanticFaceFromRef, supportsInterfacePreview } from '../src/domain/interfaceGeometry.ts';

const bounds = { min: [0, 0, 0], max: [100, 50, 200] };
const item = (geometryRefs, extra = {}) => ({ id: 'i', name: '接口', type: 'plane', tags: [], geometryRefs, ...extra });

test('六类语义面均映射到 STL 包围盒外侧', () => {
  const refs = [
    ['part.face.front', 'front', 1, -0.6], ['part.face.back', 'back', 1, 50.6],
    ['part.face.left', 'left', 0, -0.6], ['part.face.right', 'right', 0, 100.6],
    ['part.endFace.start', 'start', 2, -0.6], ['part.endFace.end', 'end', 2, 200.6],
  ];
  for (const [reference, face, axis, position] of refs) {
    assert.equal(semanticFaceFromRef(reference), face);
    const highlight = interfaceHighlights(bounds, item([reference]))[0];
    assert.equal(highlight.face, face);
    assert.ok(highlight.corners.every(corner => Math.abs(corner[axis] - position) < 1e-9));
    assert.ok(highlight.corners.every(corner => corner.every(Number.isFinite)));
  }
});

test('矩形接口区域按 U/V 中点和尺寸落在对应语义面', () => {
  const [highlight] = interfaceHighlights(bounds, item(['part.face.front'], { region: { mode: 'rectangle', uStart: 20, vStart: 40, uSpan: 10, vSpan: 20 } }));
  assert.deepEqual(highlight.corners, [[15, -0.6, 30], [25, -0.6, 30], [25, -0.6, 50], [15, -0.6, 50]]);
  assert.equal(highlight.region, true);
});

test('feature-derived 接口按唯一实例显示多个区域，空实例不伪造高亮', () => {
  const feature = item(['legacy.ref'], { id: 'slot', declarationMode: 'featureDerived' });
  const resolved = [
    { id: 'slot.1', sourceInterfaceId: 'slot', geometryRefs: ['part.face.front'], region: { mode: 'rectangle', uStart: 20, vStart: 30, uSpan: 8, vSpan: 10 } },
    { id: 'slot.2', sourceInterfaceId: 'slot', geometryRefs: ['part.face.front'], region: { mode: 'rectangle', uStart: 70, vStart: 30, uSpan: 8, vSpan: 10 } },
  ];
  assert.equal(interfaceHighlights(bounds, feature, resolved).length, 2);
  const firstOccurrence = interfaceHighlights(bounds, { ...feature, id: 'slot.1' }, resolved);
  assert.equal(firstOccurrence.length, 1);
  assert.equal((firstOccurrence[0].corners[0][0] + firstOccurrence[0].corners[1][0]) / 2, 20);
  assert.equal(supportsInterfacePreview(feature, resolved), true);
  const unresolvedLocations = resolved.map(entry => ({ ...entry, region: null }));
  assert.deepEqual(interfaceHighlights(bounds, feature, unresolvedLocations), []);
  assert.equal(supportsInterfacePreview(feature, unresolvedLocations), false);
  assert.deepEqual(interfaceHighlights(bounds, feature, []), []);
  assert.equal(supportsInterfacePreview(feature, []), false);
});

test('未知几何引用只保留接口数据，不产生虚假定位', () => {
  const unknown = item(['part.face.unknown']);
  assert.deepEqual(interfaceHighlights(bounds, unknown), []);
  assert.equal(supportsInterfacePreview(unknown), false);
});

test('接口选中状态同时使用成员 ID 和接口实例 ID', () => {
  const members = [
    { id: 'member-a', interfaces: [{ id: 'slot.1' }] },
    { id: 'member-b', interfaces: [{ id: 'slot.1' }] },
  ];
  assert.equal(sameInterfaceSelection({ memberId: 'member-a', interfaceId: 'slot.1' }, { memberId: 'member-b', interfaceId: 'slot.1' }), false);
  assert.equal(hasInterfaceSelection(members, { memberId: 'member-a', interfaceId: 'slot.1' }), true);
  assert.equal(hasInterfaceSelection(members, { memberId: 'member-a', interfaceId: 'slot.2' }), false);
});
