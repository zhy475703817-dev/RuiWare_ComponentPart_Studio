import assert from 'node:assert/strict';
import test from 'node:test';
import { Box3, Euler, OrthographicCamera, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { boundsInSelection, projectBoxBounds, rotationDelta, selectionPivot, selectionRectangle, transformSelection, updateSelection } from '../src/viewport/selectionGeometry.ts';

test('左向右框选完全包含，右向左框选允许相交', () => {
  const window = selectionRectangle({ x: 10, y: 10 }, { x: 50, y: 50 });
  const crossing = selectionRectangle({ x: 50, y: 50 }, { x: 10, y: 10 });
  assert.equal(window.crossing, false);
  assert.equal(crossing.crossing, true);
  assert.equal(boundsInSelection({ left: 20, top: 20, right: 40, bottom: 40 }, window), true);
  assert.equal(boundsInSelection({ left: 0, top: 20, right: 30, bottom: 60 }, window), false);
  assert.equal(boundsInSelection({ left: 0, top: 20, right: 30, bottom: 60 }, crossing), true);
  assert.equal(boundsInSelection({ left: 51, top: 0, right: 80, bottom: 60 }, crossing), false);
  assert.equal(boundsInSelection(null, crossing), false);
  assert.equal(boundsInSelection({ left: NaN, top: 0, right: 80, bottom: 60 }, crossing), false);
});

test('替换、Shift 加选和 Ctrl 减选保持稳定且不重复', () => {
  assert.deepEqual(updateSelection(['a', 'b'], ['b', 'c', 'c']), ['b', 'c']);
  assert.deepEqual(updateSelection(['a', 'b'], ['b', 'c', 'c'], 'add'), ['a', 'b', 'c']);
  assert.deepEqual(updateSelection(['a', 'b', 'c'], ['a', 'c'], 'remove'), ['b']);
  assert.deepEqual(updateSelection(['a'], [], 'add'), ['a']);
  assert.deepEqual(updateSelection(['a'], []), []);
});

test('透视框选投影使用模型屏幕范围并排除镜头背面和远裁剪外模型', () => {
  const camera = new PerspectiveCamera(90, 1, 1, 100);
  camera.updateMatrixWorld();
  const bounds = projectBoxBounds(new Box3(new Vector3(-1, -1, -10), new Vector3(1, 1, -9)), camera, { width: 100, height: 100 });
  assert.ok(Math.abs(bounds.left - 44.44444444444444) < 1e-8);
  assert.ok(Math.abs(bounds.bottom - 55.55555555555556) < 1e-8);
  assert.equal(projectBoxBounds(new Box3(new Vector3(-1, -1, 1), new Vector3(1, 1, 10)), camera, { width: 100, height: 100 }), null);
  assert.equal(projectBoxBounds(new Box3(new Vector3(-1, -1, -200), new Vector3(1, 1, -101)), camera, { width: 100, height: 100 }), null);
  assert.equal(projectBoxBounds(new Box3(), camera, { width: 100, height: 100 }), null);
  assert.equal(projectBoxBounds(new Box3(new Vector3(), new Vector3(1, 1, 1)), camera, { width: 0, height: 100 }), null);
});

test('穿过近平面的模型投影有限，正交相机同样可用', () => {
  const camera = new PerspectiveCamera(90, 1, 1, 100);
  camera.updateMatrixWorld();
  const bounds = projectBoxBounds(new Box3(new Vector3(-1, -1, -3), new Vector3(1, 1, 1)), camera, { width: 100, height: 100 });
  assert.ok(Object.values(bounds).every(Number.isFinite));
  assert.ok(Math.abs(bounds.left) < 1e-8);
  const orthographic = new OrthographicCamera(-10, 10, 10, -10, 1, 100);
  orthographic.position.set(0, 0, 20);
  orthographic.updateMatrixWorld();
  assert.deepEqual(projectBoxBounds(new Box3(new Vector3(-2, -2, 0), new Vector3(2, 2, 2)), orthographic, { width: 100, height: 100 }), { left: 40, top: 40, right: 60, bottom: 60 });
});

test('多选公共枢轴与世界旋转保持相对距离和原有姿态', () => {
  const members = [
    { id: 'a', position: [0, 0, 0], rotation: [.2, .4, .6] },
    { id: 'b', position: [20, 0, 0], rotation: [0, 0, 0] },
  ];
  const pivot = selectionPivot(members);
  assert.deepEqual(pivot, [10, 0, 0]);
  const transformed = transformSelection(members, pivot, [100, 200, 30], [0, 0, Math.PI / 2]);
  assert.ok(new Vector3(...transformed[0].position).distanceTo(new Vector3(100, 190, 30)) < 1e-8);
  assert.ok(new Vector3(...transformed[1].position).distanceTo(new Vector3(100, 210, 30)) < 1e-8);
  assert.ok(Math.abs(new Vector3(...transformed[0].position).distanceTo(new Vector3(...transformed[1].position)) - 20) < 1e-8);
  const expected = new Quaternion().setFromEuler(new Euler(0, 0, Math.PI / 2)).multiply(new Quaternion().setFromEuler(new Euler(...members[0].rotation)));
  assert.ok(expected.angleTo(new Quaternion().setFromEuler(new Euler(...transformed[0].rotation))) < 1e-7);
  assert.deepEqual(members[0].position, [0, 0, 0]);
  assert.deepEqual(selectionPivot([]), [0, 0, 0]);
});

test('多选精确旋转保持已有复合姿态并准确达到输入角度', () => {
  const members = [
    { id: 'active', position: [2, 3, 4], rotation: [.4, .6, -.5] },
    { id: 'other', position: [10, -2, 6], rotation: [-.2, .3, .7] },
  ];
  const target = [.4, 1.1, -.5];
  const transformed = transformSelection(members, members[0].position, members[0].position, rotationDelta(members[0].rotation, target));
  const orientation = item => new Quaternion().setFromEuler(new Euler(...item.rotation));
  assert.ok(orientation(transformed[0]).angleTo(new Quaternion().setFromEuler(new Euler(...target))) < 1e-7);
  assert.ok(new Vector3(...transformed[0].position).distanceTo(new Vector3(...members[0].position)) < 1e-8);
  assert.ok(Math.abs(orientation(members[0]).angleTo(orientation(members[1])) - orientation(transformed[0]).angleTo(orientation(transformed[1]))) < 1e-7);
  assert.ok(Math.abs(new Vector3(...members[0].position).distanceTo(new Vector3(...members[1].position)) - new Vector3(...transformed[0].position).distanceTo(new Vector3(...transformed[1].position))) < 1e-8);
});
