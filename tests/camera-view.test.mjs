import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_CAMERA_STORAGE_KEY, RESET_CAMERA_DISTANCE_MM, createResetCameraView, decodeCameraView, readCameraView, writeCameraView } from '../src/viewport/cameraView.ts';

const view = { version: 1, position: [1200, -800, 600], target: [140, -80, 100], zoom: 1.25 };

function useStorage(t, storage) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else delete globalThis.localStorage;
  });
}

test('相机视角按默认或指定键保存并完整还原', t => {
  const entries = new Map();
  useStorage(t, { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) });
  assert.equal(readCameraView(), null);
  writeCameraView(view);
  assert.deepEqual(JSON.parse(entries.get(DEFAULT_CAMERA_STORAGE_KEY)), view);
  assert.deepEqual(readCameraView(), view);
  const other = { ...view, position: [-20, 50, 90], zoom: 2 };
  writeCameraView(other, 'isolated-test-camera');
  assert.deepEqual(readCameraView('isolated-test-camera'), other);
  assert.deepEqual(readCameraView(), view);
});

test('相机视角拒绝损坏 JSON、版本和向量结构', () => {
  for (const raw of [null, '', '{', 'null', '[]', '3', '"camera"']) assert.equal(decodeCameraView(raw), null);
  for (const invalid of [
    {}, { ...view, version: 0 }, { ...view, version: 2 }, { ...view, version: '1' },
    { ...view, position: [1, 2] }, { ...view, position: [1, 2, 3, 4] },
    { ...view, position: { 0: 1, 1: 2, 2: 3 } }, { ...view, target: null },
    { ...view, target: [1, '2', 3] }, { ...view, zoom: '1' },
  ]) assert.equal(decodeCameraView(JSON.stringify(invalid)), null);
});

test('相机视角拒绝非有限值、重合点和不安全投影数值', () => {
  for (const invalid of [
    { ...view, position: [NaN, 2, 3] }, { ...view, target: [1, Infinity, 3] },
    { ...view, position: [...view.target] },
    { ...view, position: [1e13, 0, 0] },
    { ...view, position: [1e-10, 0, 0], target: [0, 0, 0] },
    ...[0, -1, NaN, Infinity, 1e-9, 1e9].map(zoom => ({ ...view, zoom })),
  ]) assert.equal(decodeCameraView(JSON.stringify(invalid)), null);
  // Overflow is valid JSON syntax, but it must not reach the renderer.
  assert.equal(decodeCameraView('{"version":1,"position":[1e999,0,0],"target":[0,0,0],"zoom":1}'), null);
});

test('相机视角保留大场景、负坐标和精细变换精度', () => {
  const precise = { version: 1, position: [-1e9, 123.456789123, 1e-5], target: [-1e9 + 100, 0, -200], zoom: 0.125 };
  assert.deepEqual(decodeCameraView(JSON.stringify(precise)), precise);
});

test('无效相机状态不会覆盖上一次有效视角', t => {
  const entries = new Map([[DEFAULT_CAMERA_STORAGE_KEY, JSON.stringify(view)]]);
  useStorage(t, { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) });
  writeCameraView({ ...view, target: [...view.position] });
  writeCameraView({ ...view, zoom: NaN });
  assert.deepEqual(readCameraView(), view);
});

test('损坏或拒绝访问的存储不会中断视图', t => {
  useStorage(t, { getItem: () => '{broken', setItem: () => { throw new Error('QuotaExceededError'); } });
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
});

test('未提供浏览器存储时允许初始化和保存', t => {
  useStorage(t, undefined);
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
});

const distance = (a, b = [0, 0, 0]) => Math.hypot(...a.map((value, index) => value - b[index]));
const approximately = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} differs from ${expected}`);

test('视角复位从远近和平移状态恢复相同距离并保留观察方向', () => {
  const nearPosition = [3, -4, 2];
  const farPosition = [3e7, -4e7, 2e7];
  const shiftedPosition = [1003, -704, 902];
  const shiftedTarget = [1000, -700, 900];
  const near = createResetCameraView(nearPosition, [0, 0, 0], 16 / 9);
  const far = createResetCameraView(farPosition, [0, 0, 0], 16 / 9);
  const shifted = createResetCameraView(shiftedPosition, shiftedTarget, 16 / 9);
  approximately(distance(near.position), RESET_CAMERA_DISTANCE_MM);
  approximately(distance(near.position, far.position), 0);
  approximately(distance(near.position, shifted.position), 0);
  near.position.forEach((value, index) => approximately(value / RESET_CAMERA_DISTANCE_MM, nearPosition[index] / Math.sqrt(29)));
  for (const result of [near, far, shifted]) {
    assert.deepEqual(result.target, [0, 0, 0]);
    assert.equal(result.zoom, 1);
  }
  assert.deepEqual(nearPosition, [3, -4, 2]);
  assert.deepEqual(shiftedPosition, [1003, -704, 902]);
  assert.deepEqual(shiftedTarget, [1000, -700, 900]);
});

test('视角复位以径向毫米距离适配宽窄画布', () => {
  for (const aspect of [16 / 9, 2, 32 / 9]) {
    const result = createResetCameraView([1, -1, 1], [0, 0, 0], aspect);
    approximately(distance(result.position), 16000);
  }
  for (const aspect of [1, 3 / 4, .5]) {
    const result = createResetCameraView([1, -1, 1], [0, 0, 0], aspect);
    approximately(distance(result.position), 16000 * (16 / 9) / aspect);
  }
  for (const aspect of [0, -1, NaN, Infinity]) {
    approximately(distance(createResetCameraView([1, -1, 1], [0, 0, 0], aspect).position), 16000);
  }
});

test('视角复位为退化或非法方向提供有限默认视角', () => {
  const expected = createResetCameraView([1, -1, 1], [0, 0, 0], 16 / 9);
  for (const [position, target] of [
    [[0, 0, 0], [0, 0, 0]],
    [[20, 30, 40], [20, 30, 40]],
    [[1e-5, 0, 0], [0, 0, 0]],
    [[NaN, 2, 3], [0, 0, 0]],
    [[1, 2, Infinity], [0, 0, 0]],
    [[1, 2, 3], [0, -Infinity, 0]],
  ]) {
    const result = createResetCameraView(position, target, 16 / 9);
    assert.deepEqual(result, expected);
    assert.ok(result.position.every(Number.isFinite));
  }
});

test('重复视角复位不漂移且复位结果可以保存恢复', t => {
  const entries = new Map();
  useStorage(t, { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) });
  for (const aspect of [2, 1]) {
    const initial = createResetCameraView([3000, -2500, 1900], [450, 80, 170], aspect);
    let result = initial;
    for (let index = 0; index < 25; index++) result = createResetCameraView(result.position, result.target, aspect);
    approximately(distance(result.position, initial.position), 0);
    writeCameraView(result);
    assert.deepEqual(readCameraView(), result);
    assert.deepEqual(decodeCameraView(JSON.stringify(result)), result);
  }
});
